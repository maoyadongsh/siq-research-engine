import re
from datetime import timedelta

import anyio
from services.auth_service import User, UserRole
from services.meeting_contracts import (
    MEETING_TABLES,
    MeetingEvent,
    MeetingJob,
    MeetingModelSnapshot,
    MeetingSession,
    MeetingStreamLease,
    utcnow,
)
from services.meeting_metrics import (
    meeting_stream_closed,
    meeting_stream_opened,
    observe_meeting_latency,
    record_meeting_counter,
    render_meeting_database_metrics,
    render_meeting_process_metrics,
)
from services.meeting_native_capture_contracts import (
    MEETING_NATIVE_CAPTURE_TABLES,
    MeetingNativeCapture,
    MeetingNativeCaptureBatch,
    MeetingNativeCaptureFinalization,
    MeetingNativeCaptureGap,
    NativeCaptureFinalizationState,
    NativeCaptureGapReason,
)
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession


def _assert_no_sensitive_metric_labels(rendered: str) -> None:
    label_names = set(re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)="', rendered))
    assert label_names.isdisjoint(
        {
            "capture_id",
            "meeting_id",
            "owner_user_id",
            "user_id",
            "token",
            "device_id",
            "device_installation_id",
            "storage_key",
            "path",
        }
    )


def test_process_metrics_have_only_bounded_labels_and_no_object_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED", "1")
    monkeypatch.setenv("SIQ_MEETINGS_ENABLED", "1")
    monkeypatch.setenv("SIQ_MEETING_NATIVE_CAPTURE_ROOT", str(tmp_path))
    monkeypatch.setenv("SIQ_MEETING_NATIVE_CAPTURE_MAX_TOTAL_BYTES", str(4 * 1024**3))
    meeting_stream_opened()
    record_meeting_counter("audio_frame", "persisted")
    record_meeting_counter("audio_gap", "sequence gap / owner-123")
    record_meeting_counter("native_capture_auth_failure", "token/secret-value")
    record_meeting_counter("native_capture_auth_failure", "device_mismatch")
    record_meeting_counter("native_capture_batch", "capture-id-should-not-be-a-label")
    record_meeting_counter("native_capture_batch", "accepted")
    record_meeting_counter("native_capture_batch_bytes", "accepted", amount=3_200)
    record_meeting_counter("native_capture_storage_rejection", "unavailable")
    record_meeting_counter("final_asr_window", "succeeded")
    record_meeting_counter("final_asr_window", "meeting-id-must-not-be-a-label")
    record_meeting_counter("unknown_metric", "owner-123")
    observe_meeting_latency("segment_persist_latency_seconds", 0.125)
    observe_meeting_latency("final_asr_window_processing_seconds", 12.5)
    observe_meeting_latency("final_asr_job_processing_seconds", 180.0)
    rendered = render_meeting_process_metrics()
    meeting_stream_closed()

    assert "meeting_active_sessions 1" in rendered
    assert 'meeting_audio_frame_total{result="persisted"}' in rendered
    assert 'meeting_audio_gap_total{reason="other"}' in rendered
    assert 'meeting_native_capture_auth_failure_total{reason="other"}' in rendered
    assert 'meeting_native_capture_auth_failure_total{reason="device_mismatch"}' in rendered
    assert 'meeting_native_capture_batch_total{result="other"}' in rendered
    assert 'meeting_native_capture_batch_total{result="accepted"}' in rendered
    assert 'meeting_native_capture_batch_bytes_total{result="accepted"}' in rendered
    assert 'meeting_native_capture_storage_rejection_total{reason="unavailable"}' in rendered
    assert 'meeting_final_asr_window_total{result="succeeded"}' in rendered
    assert 'meeting_final_asr_window_total{result="other"}' in rendered
    assert "meeting_native_capture_operational 1" in rendered
    assert "meeting_native_capture_storage_probe_success 1" in rendered
    assert re.search(r"meeting_native_capture_storage_free_bytes [1-9]\d*", rendered)
    assert "meeting_native_capture_storage_required_free_bytes 8589934592" in rendered
    assert re.search(r"meeting_segment_persist_latency_seconds_count [1-9]\d*", rendered)
    assert re.search(r"meeting_final_asr_window_processing_seconds_count [1-9]\d*", rendered)
    assert 'meeting_final_asr_window_processing_seconds_bucket{le="15"}' in rendered
    assert re.search(r"meeting_final_asr_job_processing_seconds_count [1-9]\d*", rendered)
    assert 'meeting_final_asr_job_processing_seconds_bucket{le="300"}' in rendered
    assert "meeting_caption_blocked_by_postprocess_total" in rendered
    assert "owner-123" not in rendered
    assert "secret-value" not in rendered
    assert "capture-id-should-not-be-a-label" not in rendered
    assert "meeting-id-must-not-be-a-label" not in rendered
    assert "unknown_metric" not in rendered
    assert str(tmp_path) not in rendered
    _assert_no_sensitive_metric_labels(rendered)


def test_database_metrics_aggregate_without_meeting_user_or_model_labels(tmp_path):
    async def scenario() -> str:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'metrics.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[User.__table__, *[model.__table__ for model in MEETING_TABLES]],
                )
            )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            user = User(
                id=71,
                username="meeting-metrics-user",
                email="meeting-metrics@example.test",
                hashed_password="x",
                full_name="Sensitive Person",
                role=UserRole.ANALYST,
                is_active=True,
                approval_status="approved",
            )
            meeting = MeetingSession(owner_user_id=71, title="Secret board meeting")
            snapshot = MeetingModelSnapshot(
                meeting_id=meeting.id,
                model_ref="secret-model-ref",
                selection_mode="pinned",
                resolved_provider="private-provider",
                resolved_model="private-model",
                provider_locality="local",
                hermes_target="secret-target",
                meeting_profile_version="v1",
                prompt_version="v1",
                settings_version=1,
            )
            session.add_all(
                [
                    user,
                    meeting,
                    MeetingStreamLease(
                        meeting_id=meeting.id,
                        stream_epoch=1,
                        connection_id="secret-connection-id",
                        owner_user_id=71,
                        lease_until=utcnow() + timedelta(minutes=1),
                    ),
                    snapshot,
                    MeetingJob(
                        meeting_id=meeting.id,
                        job_kind="rolling_minutes",
                        idempotency_key="secret-job-key",
                        model_snapshot_id=snapshot.id,
                    ),
                    MeetingEvent(
                        meeting_id=meeting.id,
                        cursor=1,
                        event_type="audio.gap.detected",
                    ),
                ]
            )
            await session.commit()
            rendered = await render_meeting_database_metrics(session)
        await engine.dispose()
        return rendered

    rendered = anyio.run(scenario)
    assert "meeting_active_leases 1" in rendered
    assert 'meeting_postprocess_queue_depth{kind="rolling_minutes"} 1' in rendered
    assert 'meeting_ai_job_total{kind="rolling_minutes",status="queued",locality="local"} 1' in rendered
    assert 'meeting_audio_gap_durable_total{reason="recorded"} 1' in rendered
    assert "meeting_native_capture_durable_batch_count" not in rendered
    for secret in (
        "Secret board meeting",
        "Sensitive Person",
        "secret-model-ref",
        "private-provider",
        "private-model",
        "secret-target",
        "secret-connection-id",
        "secret-job-key",
    ):
        assert secret not in rendered
    _assert_no_sensitive_metric_labels(rendered)


def test_native_capture_database_metrics_are_aggregate_and_backward_compatible(tmp_path):
    async def scenario() -> str:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'native-metrics.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=[
                        User.__table__,
                        *[model.__table__ for model in MEETING_TABLES],
                        *[model.__table__ for model in MEETING_NATIVE_CAPTURE_TABLES],
                    ],
                )
            )
        async with AsyncSession(engine, expire_on_commit=False) as session:
            user = User(
                id=72,
                username="native-metrics-user",
                email="native-metrics@example.test",
                hashed_password="x",
                full_name="Native Metrics Person",
                role=UserRole.ANALYST,
                is_active=True,
                approval_status="approved",
            )
            meeting = MeetingSession(owner_user_id=72, title="Sensitive native meeting")
            pending_capture = MeetingNativeCapture(
                id="sensitive-pending-capture",
                meeting_id=meeting.id,
                owner_user_id=72,
                device_installation_hash="a" * 64,
                create_idempotency_key="sensitive-create-key-one",
                create_request_hash="b" * 64,
                max_total_bytes=64_000,
                max_duration_samples=64_000,
            )
            failed_capture = MeetingNativeCapture(
                id="sensitive-failed-capture",
                meeting_id=meeting.id,
                owner_user_id=72,
                device_installation_hash="c" * 64,
                create_idempotency_key="sensitive-create-key-two",
                create_request_hash="d" * 64,
                max_total_bytes=64_000,
                max_duration_samples=64_000,
            )
            session.add_all(
                [
                    user,
                    meeting,
                    pending_capture,
                    failed_capture,
                    MeetingNativeCaptureBatch(
                        capture_id=pending_capture.id,
                        meeting_id=meeting.id,
                        owner_user_id=72,
                        stream_epoch=1,
                        sequence=0,
                        first_sample=0,
                        sample_count=1_600,
                        end_sample=1_600,
                        captured_monotonic_ns=123,
                        encoding="pcm_s16le",
                        sample_rate=16_000,
                        channels=1,
                        byte_size=3_200,
                        sha256="e" * 64,
                        storage_key="72/sensitive-pending-capture/1/0.pcm",
                        manifest_revision=1,
                        idempotency_key="sensitive-batch-key",
                    ),
                    MeetingNativeCaptureGap(
                        capture_id=pending_capture.id,
                        meeting_id=meeting.id,
                        owner_user_id=72,
                        stream_epoch=1,
                        from_sequence=1,
                        to_sequence=1,
                        start_sample=1_600,
                        end_sample=3_200,
                        reason=NativeCaptureGapReason.DEVICE_STORAGE_LOST.value,
                        manifest_revision=1,
                        idempotency_key="sensitive-gap-key",
                        request_hash="f" * 64,
                    ),
                    MeetingNativeCaptureFinalization(
                        capture_id=pending_capture.id,
                        meeting_id=meeting.id,
                        owner_user_id=72,
                        state=NativeCaptureFinalizationState.PENDING_UPLOAD.value,
                        created_at=utcnow() - timedelta(minutes=20),
                        updated_at=utcnow() - timedelta(minutes=20),
                    ),
                    MeetingNativeCaptureFinalization(
                        capture_id=failed_capture.id,
                        meeting_id=meeting.id,
                        owner_user_id=72,
                        state=NativeCaptureFinalizationState.FAILED.value,
                        public_error_code="SENSITIVE_INTERNAL_STORAGE_FAILURE",
                        internal_diagnostic="/secret/native/path token=secret-token",
                    ),
                ]
            )
            await session.commit()
            rendered = await render_meeting_database_metrics(session)
        await engine.dispose()
        return rendered

    rendered = anyio.run(scenario)
    assert "meeting_native_capture_durable_batch_count 1" in rendered
    assert "meeting_native_capture_durable_batch_bytes 3200" in rendered
    assert 'meeting_native_capture_gap_durable_total{reason="device_storage_lost"} 1' in rendered
    assert "meeting_native_capture_pending_upload 1" in rendered
    assert "meeting_native_capture_finalization_failed 1" in rendered
    assert 'meeting_native_capture_finalization_backlog{state="pending_upload"} 1' in rendered
    assert re.search(
        r'meeting_native_capture_finalization_oldest_age_seconds\{state="pending_upload"\} '
        r"1[12]\d\d\.\d{3}",
        rendered,
    )
    for secret in (
        "sensitive-pending-capture",
        "sensitive-failed-capture",
        "Sensitive native meeting",
        "Native Metrics Person",
        "sensitive-batch-key",
        "sensitive-gap-key",
        "SENSITIVE_INTERNAL_STORAGE_FAILURE",
        "/secret/native/path",
        "secret-token",
    ):
        assert secret not in rendered
    _assert_no_sensitive_metric_labels(rendered)
