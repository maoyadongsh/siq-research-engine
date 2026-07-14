import hashlib
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers.meeting_native_captures import router as native_router
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole
from services.meeting_config import MeetingSettings, meeting_capabilities
from services.meeting_contracts import (
    MEETING_TABLES,
    MeetingEvent,
    MeetingSession,
    MeetingStreamLease,
    MeetingStreamTicket,
    utcnow,
)
from services.meeting_database import get_meeting_async_session
from services.meeting_native_capture_config import MeetingNativeCaptureSettings
from services.meeting_native_capture_contracts import (
    MEETING_NATIVE_CAPTURE_TABLES,
    MeetingNativeCapture,
    MeetingNativeCaptureBatch,
    MeetingNativeCaptureToken,
    NativeCaptureBatchMetadata,
    NativeCaptureBoundaryRequest,
    NativeCaptureCaptureCheckpoint,
    NativeCaptureCreateRequest,
    NativeCaptureFinalizationCheckpoint,
    NativeCaptureIngestCheckpoint,
    NativeCaptureManifestEntry,
    NativeCaptureRealtimeCheckpoint,
    native_capture_manifest_sha256,
)
from services.meeting_native_capture_service import (
    MeetingNativeCaptureConflict,
    MeetingNativeCaptureForbidden,
    MeetingNativeCaptureRepository,
    MeetingNativeCaptureTooLarge,
    MeetingNativeCaptureUnauthorized,
    MeetingNativeCaptureUnavailable,
    _missing_integer_ranges,
)
from services.meeting_native_capture_storage import (
    MeetingNativeCaptureStorage,
    MeetingNativeCaptureStorageError,
)
from services.meeting_repository import MeetingRepository
from services.meeting_stream_ticket import MeetingStreamTicketService, StreamTicketError
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


async def _database():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
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
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _native_settings(tmp_path: Path) -> MeetingNativeCaptureSettings:
    return MeetingNativeCaptureSettings(
        enabled=True,
        root=tmp_path / "native-captures",
        token_ttl_seconds=300,
        max_batch_bytes=64,
        max_total_bytes=128,
        max_duration_seconds=60,
        max_active_per_owner=2,
    )


def _meeting_settings() -> MeetingSettings:
    return MeetingSettings(
        enabled=True,
        asr_enabled=True,
        stream_ticket_ttl_seconds=45,
        stream_lease_ttl_seconds=20,
        reconnect_window_seconds=60,
    )


async def _seed(factory):
    async with factory() as session:
        session.add_all(
            [
                User(
                    id=7,
                    username="native-owner",
                    email="native-owner@example.test",
                    hashed_password="x",
                    full_name="Native Owner",
                    role=UserRole.ANALYST,
                    is_active=True,
                ),
                User(
                    id=8,
                    username="native-other",
                    email="native-other@example.test",
                    hashed_password="x",
                    full_name="Native Other",
                    role=UserRole.ANALYST,
                    is_active=True,
                ),
            ]
        )
        meeting = MeetingSession(
            owner_user_id=7,
            title="Native capture",
            state="live",
            stream_epoch=1,
            ai_enabled=False,
            selection_mode="none",
        )
        other = MeetingSession(
            owner_user_id=8,
            title="Other native capture",
            state="live",
            stream_epoch=1,
            ai_enabled=False,
            selection_mode="none",
        )
        session.add_all([meeting, other])
        await session.commit()
        return meeting.id, other.id


async def _stream(payload: bytes):
    midpoint = max(1, len(payload) // 2)
    yield payload[:midpoint]
    yield payload[midpoint:]


def _metadata(payload: bytes, *, first: int, count: int, key: str, revision: int = 1):
    return NativeCaptureBatchMetadata(
        first_sample=first,
        sample_count=count,
        captured_monotonic_ns=1_000_000_000 + first * 62_500,
        encoding="pcm_s16le",
        sample_rate=16_000,
        channels=1,
        sha256=hashlib.sha256(payload).hexdigest(),
        manifest_revision=revision,
        idempotency_key=key,
        content_length=len(payload),
    )


def _manifest_entry(payload: bytes, *, first: int, count: int, sequence: int):
    return NativeCaptureManifestEntry(
        sequence=sequence,
        first_sample=first,
        sample_count=count,
        captured_monotonic_ns=1_000_000_000 + first * 62_500,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _boundary(
    epoch: int,
    sequence: int,
    samples: int,
    revision: int = 2,
    *,
    entries: list[NativeCaptureManifestEntry] | None = None,
):
    manifest_entries = entries or []
    return NativeCaptureBoundaryRequest(
        expected_epoch=epoch,
        final_sequence=sequence,
        recorded_through_sample=samples,
        manifest_revision=revision,
        manifest_sha256=native_capture_manifest_sha256(
            expected_epoch=epoch,
            final_sequence=sequence,
            recorded_through_sample=samples,
            manifest_revision=revision,
            entries=manifest_entries,
        ),
        manifest_entries=manifest_entries,
    )


def test_native_capture_checkpoint_models_and_durable_event_cursor(tmp_path):
    async def scenario():
        engine, factory = await _database()
        meeting_id, _ = await _seed(factory)
        native = _native_settings(tmp_path)
        async with factory() as session:
            repository = MeetingNativeCaptureRepository(
                session,
                MeetingNativeCaptureStorage(native.root),
                native,
                meeting_settings=_meeting_settings(),
            )
            created = await repository.create(
                meeting_id,
                7,
                NativeCaptureCreateRequest(device_installation_id="device-installation-checkpoint"),
                idempotency_key="native-checkpoint-shapes",
            )
            await session.exec(delete(MeetingEvent).where(MeetingEvent.meeting_id == meeting_id))
            await session.commit()

            checkpoint = await repository.checkpoint(
                meeting_id,
                created.capture.id,
                created.capture_token,
            )
            assert isinstance(checkpoint.capture_checkpoint, NativeCaptureCaptureCheckpoint)
            assert isinstance(checkpoint.ingest_checkpoint, NativeCaptureIngestCheckpoint)
            assert isinstance(checkpoint.realtime_checkpoint, NativeCaptureRealtimeCheckpoint)
            assert isinstance(checkpoint.finalization_checkpoint, NativeCaptureFinalizationCheckpoint)
            payload = checkpoint.model_dump(mode="json")
            assert payload["capture_checkpoint"] == {
                "state": "active",
                "recorded_through_sample": None,
                "last_sealed_epoch": None,
                "manifest_revision": None,
            }
            assert payload["ingest_checkpoint"] == {
                "persisted_through_sample": 0,
                "accounted_through_sample": 0,
                "highest_received_sample": 0,
                "received_batches": 0,
                "received_bytes": 0,
                "missing_sample_ranges": [],
                "audio_missing_sample_ranges": [],
                "accepted_gaps": 0,
                "ingest_complete": False,
            }
            assert payload["realtime_checkpoint"] == {
                "stream_epoch": 1,
                "last_acked_sequence": -1,
                "stable_ordinal": 0,
                "event_cursor": 0,
            }
            assert payload["finalization_checkpoint"] == {
                "capture_sealed": False,
                "ingest_complete": False,
                "has_unrecoverable_gaps": False,
                "packaging_state": None,
                "packaging_attempt": 0,
                "packaging_error_code": None,
                "wav_sha256": None,
                "wav_byte_size": None,
                "server_playback_state": "not_ready",
                "postprocess_state": "not_started",
            }

            session.add_all(
                [
                    MeetingEvent(meeting_id=meeting_id, cursor=3, event_type="checkpoint.test"),
                    MeetingEvent(meeting_id=meeting_id, cursor=9, event_type="checkpoint.test"),
                ]
            )
            await session.commit()
            durable = await repository.checkpoint(
                meeting_id,
                created.capture.id,
                created.capture_token,
            )
            assert durable.realtime_checkpoint.event_cursor == 9
        await engine.dispose()

    anyio.run(scenario)


def test_native_capture_batches_checkpoint_seal_and_token_hashing(tmp_path):
    async def scenario():
        engine, factory = await _database()
        meeting_id, _ = await _seed(factory)
        native = _native_settings(tmp_path)
        storage = MeetingNativeCaptureStorage(native.root)
        async with factory() as session:
            repository = MeetingNativeCaptureRepository(
                session,
                storage,
                native,
                meeting_settings=_meeting_settings(),
            )
            created = await repository.create(
                meeting_id,
                7,
                NativeCaptureCreateRequest(device_installation_id="device-installation-0001"),
                idempotency_key="native-start-1",
            )
            capture_id = created.capture.id
            first_token = created.capture_token
            replayed_start = await repository.create(
                meeting_id,
                7,
                NativeCaptureCreateRequest(device_installation_id="device-installation-0001"),
                idempotency_key="native-start-1",
            )
            assert replayed_start.replayed is True
            assert replayed_start.capture.id == capture_id
            assert replayed_start.capture_token != first_token
            with pytest.raises(MeetingNativeCaptureUnauthorized):
                await repository.checkpoint(meeting_id, capture_id, first_token)
            with pytest.raises(MeetingNativeCaptureConflict) as start_conflict:
                await repository.create(
                    meeting_id,
                    7,
                    NativeCaptureCreateRequest(device_installation_id="device-installation-changed"),
                    idempotency_key="native-start-1",
                )
            assert start_conflict.value.code == "NATIVE_CAPTURE_IDEMPOTENCY_CONFLICT"
            raw_token = replayed_start.capture_token
            stored_tokens = list((await session.exec(select(MeetingNativeCaptureToken))).all())
            assert len(stored_tokens) == 2
            assert {value.token_hash for value in stored_tokens} == {
                hashlib.sha256(first_token.encode("ascii")).hexdigest(),
                hashlib.sha256(raw_token.encode("ascii")).hexdigest(),
            }
            assert all(first_token not in value.scopes_json for value in stored_tokens)
            assert replayed_start.scopes == ["batch:write", "checkpoint:read", "capture:seal"]

            late = b"\x02\x00" * 16
            response = await repository.put_batch(
                meeting_id,
                capture_id,
                raw_token,
                1,
                1,
                _metadata(late, first=16, count=16, key="batch-1"),
                _stream(late),
            )
            assert response.checkpoint.ingest_checkpoint.persisted_through_sample == 0
            assert response.checkpoint.ingest_checkpoint.missing_sample_ranges == [{"start": 0, "end": 16}]
            assert response.checkpoint.epochs[0].missing_sequence_ranges == [{"start": 0, "end": 0}]
            persisted_late = (await session.exec(select(MeetingNativeCaptureBatch))).one()
            assert persisted_late.captured_monotonic_ns == 1_001_000_000

            first = b"\x01\x00" * 16
            response = await repository.put_batch(
                meeting_id,
                capture_id,
                raw_token,
                1,
                0,
                _metadata(first, first=0, count=16, key="batch-0"),
                _stream(first),
            )
            assert response.checkpoint.ingest_checkpoint.persisted_through_sample == 32
            replay = await repository.put_batch(
                meeting_id,
                capture_id,
                raw_token,
                1,
                0,
                _metadata(first, first=0, count=16, key="retry-key"),
                _stream(first),
            )
            assert replay.replayed is True
            assert len((await session.exec(select(MeetingNativeCaptureBatch))).all()) == 2
            with pytest.raises(MeetingNativeCaptureConflict) as crossed_key:
                await repository.put_batch(
                    meeting_id,
                    capture_id,
                    raw_token,
                    1,
                    0,
                    _metadata(first, first=0, count=16, key="batch-1"),
                    _stream(first),
                )
            assert crossed_key.value.code == "NATIVE_CAPTURE_IDEMPOTENCY_CONFLICT"

            changed = b"\x03\x00" * 16
            with pytest.raises(MeetingNativeCaptureConflict) as conflict:
                await repository.put_batch(
                    meeting_id,
                    capture_id,
                    raw_token,
                    1,
                    0,
                    _metadata(changed, first=0, count=16, key="changed"),
                    _stream(changed),
                )
            assert conflict.value.code == "NATIVE_CAPTURE_BATCH_CONFLICT"
            overlap = b"\x04\x00" * 16
            with pytest.raises(MeetingNativeCaptureConflict) as overlap_error:
                await repository.put_batch(
                    meeting_id,
                    capture_id,
                    raw_token,
                    1,
                    2,
                    _metadata(overlap, first=0, count=16, key="overlap"),
                    _stream(overlap),
                )
            assert overlap_error.value.code == "NATIVE_CAPTURE_SAMPLE_RANGE_CONFLICT"

            manifest = [
                _manifest_entry(first, first=0, count=16, sequence=0),
                _manifest_entry(late, first=16, count=16, sequence=1),
            ]
            sealed = await repository.seal(
                meeting_id,
                capture_id,
                raw_token,
                _boundary(1, 1, 32, entries=manifest),
            )
            assert sealed.capture.state == "sealed"
            assert sealed.checkpoint.ingest_checkpoint.ingest_complete is True
            assert sealed.checkpoint.finalization_checkpoint.server_playback_state == "pending_packaging"
            replayed_seal = await repository.seal(
                meeting_id,
                capture_id,
                raw_token,
                _boundary(1, 1, 32, entries=manifest),
            )
            assert replayed_seal.replayed is True
            with pytest.raises(MeetingNativeCaptureConflict):
                await repository.put_batch(
                    meeting_id,
                    capture_id,
                    raw_token,
                    1,
                    2,
                    _metadata(b"\x00\x00" * 16, first=32, count=16, key="after-seal"),
                    _stream(b"\x00\x00" * 16),
                )

            active_token = next(value for value in stored_tokens if value.revoked_at is None)
            active_token.expires_at = utcnow() - timedelta(seconds=1)
            session.add(active_token)
            await session.commit()
            with pytest.raises(MeetingNativeCaptureUnauthorized):
                await repository.checkpoint(meeting_id, capture_id, raw_token)
            renewed = await repository.renew_token(meeting_id, capture_id, 7)
            await repository.checkpoint(meeting_id, capture_id, renewed.capture_token)
            await repository.revoke_tokens(meeting_id, capture_id, 7)
            with pytest.raises(MeetingNativeCaptureUnauthorized):
                await repository.checkpoint(meeting_id, capture_id, renewed.capture_token)
        await engine.dispose()

    anyio.run(scenario)


@pytest.mark.parametrize(
    ("mutation", "expected_error", "expected_code"),
    [
        ("revoke", MeetingNativeCaptureUnauthorized, "NATIVE_CAPTURE_TOKEN_REVOKED"),
        ("expire", MeetingNativeCaptureUnauthorized, "NATIVE_CAPTURE_TOKEN_EXPIRED"),
        ("scope", MeetingNativeCaptureForbidden, "NATIVE_CAPTURE_SCOPE_DENIED"),
        ("device", MeetingNativeCaptureUnauthorized, "NATIVE_CAPTURE_DEVICE_MISMATCH"),
    ],
)
def test_native_capture_revalidates_token_after_capture_lock(
    tmp_path,
    mutation,
    expected_error,
    expected_code,
):
    async def scenario():
        engine, factory = await _database()
        meeting_id, _ = await _seed(factory)
        native = _native_settings(tmp_path)
        storage = MeetingNativeCaptureStorage(native.root)
        async with factory() as session:
            creator = MeetingNativeCaptureRepository(
                session,
                storage,
                native,
                meeting_settings=_meeting_settings(),
            )
            created = await creator.create(
                meeting_id,
                7,
                NativeCaptureCreateRequest(device_installation_id="device-installation-lock-test"),
                idempotency_key=f"token-lock-{mutation}",
            )

            class MutatingRepository(MeetingNativeCaptureRepository):
                mutated = False

                async def _owned_capture(
                    self,
                    owned_meeting_id: str,
                    owned_capture_id: str,
                    owner_user_id: int,
                    *,
                    lock: bool = False,
                ) -> MeetingNativeCapture:
                    capture = await super()._owned_capture(
                        owned_meeting_id,
                        owned_capture_id,
                        owner_user_id,
                        lock=lock,
                    )
                    if not lock or self.mutated:
                        return capture
                    self.mutated = True
                    if mutation == "device":
                        capture.device_installation_hash = hashlib.sha256(
                            b"another-device-installation"
                        ).hexdigest()
                        self.session.add(capture)
                    else:
                        values = {
                            "revoke": {"revoked_at": utcnow()},
                            "expire": {"expires_at": utcnow() - timedelta(seconds=1)},
                            "scope": {"scopes_json": '["batch:write"]'},
                        }[mutation]
                        await self.session.exec(
                            update(MeetingNativeCaptureToken)
                            .where(MeetingNativeCaptureToken.capture_id == owned_capture_id)
                            .values(**values)
                        )
                    await self.session.flush()
                    return capture

            repository = MutatingRepository(
                session,
                storage,
                native,
                meeting_settings=_meeting_settings(),
            )
            with pytest.raises(expected_error) as rejected:
                await repository.checkpoint(
                    meeting_id,
                    created.capture.id,
                    created.capture_token,
                    device_installation_id="device-installation-lock-test",
                )
            assert rejected.value.code == expected_code
        await engine.dispose()

    anyio.run(scenario)


def test_native_capture_and_delete_use_one_explicit_lock_order(tmp_path, monkeypatch):
    async def scenario():
        engine, factory = await _database()
        meeting_id, _ = await _seed(factory)
        settings = _native_settings(tmp_path)
        async with factory() as session:
            native = MeetingNativeCaptureRepository(
                session,
                MeetingNativeCaptureStorage(settings.root),
                settings,
                meeting_settings=_meeting_settings(),
            )
            created = await native.create(
                meeting_id,
                7,
                NativeCaptureCreateRequest(device_installation_id="device-installation-lock-order"),
                idempotency_key="native-lock-order",
            )
            original_exec = session.exec
            labels: list[str] = []

            async def tracking_exec(statement, *args, **kwargs):
                sql = str(statement).lower()
                if sql.startswith("select users.id"):
                    labels.append("user")
                elif sql.startswith("select meeting_sessions.id"):
                    labels.append("meeting")
                elif sql.startswith("select meeting_native_captures."):
                    labels.append("capture")
                return await original_exec(statement, *args, **kwargs)

            monkeypatch.setattr(session, "exec", tracking_exec)
            await native._owned_capture(meeting_id, created.capture.id, 7, lock=True)
            assert labels == ["user", "meeting", "capture"]

            labels.clear()

            async def tracking_delete_exec(statement, *args, **kwargs):
                sql = str(statement).lower()
                if sql.startswith("select users.id"):
                    labels.append("user")
                elif sql.startswith("select meeting_sessions.") and "for update" in sql:
                    labels.append("meeting")
                elif sql.startswith("update meeting_native_capture_finalizations"):
                    labels.append("finalization")
                elif sql.startswith("update meeting_native_captures"):
                    labels.append("capture")
                elif sql.startswith("update meeting_native_capture_tokens"):
                    labels.append("token")
                return await original_exec(statement, *args, **kwargs)

            monkeypatch.setattr(session, "exec", tracking_delete_exec)
            await MeetingRepository(session).request_delete(meeting_id, 7)
            assert labels[:5] == ["user", "meeting", "finalization", "capture", "token"]
        await engine.dispose()

    anyio.run(scenario)


def test_native_capture_owner_retained_byte_quota_is_cross_capture_and_replay_safe(tmp_path):
    async def scenario():
        engine, factory = await _database()
        meeting_id, _ = await _seed(factory)
        native = MeetingNativeCaptureSettings(
            enabled=True,
            root=tmp_path / "native-owner-quota",
            token_ttl_seconds=300,
            max_batch_bytes=64,
            max_total_bytes=64,
            max_retained_bytes_per_owner=96,
            max_duration_seconds=60,
            max_active_per_owner=2,
        )
        async with factory() as session:
            second_meeting = MeetingSession(
                owner_user_id=7,
                title="Second native capture",
                state="live",
                stream_epoch=1,
                ai_enabled=False,
                selection_mode="none",
            )
            session.add(second_meeting)
            await session.commit()
            repository = MeetingNativeCaptureRepository(
                session,
                MeetingNativeCaptureStorage(native.root),
                native,
                meeting_settings=_meeting_settings(),
            )
            first_capture = await repository.create(
                meeting_id,
                7,
                NativeCaptureCreateRequest(device_installation_id="device-installation-quota-1"),
                idempotency_key="owner-quota-capture-1",
            )
            second_capture = await repository.create(
                second_meeting.id,
                7,
                NativeCaptureCreateRequest(device_installation_id="device-installation-quota-2"),
                idempotency_key="owner-quota-capture-2",
            )
            first_payload = b"\x01\x00" * 32
            second_payload = b"\x02\x00" * 16
            first_metadata = _metadata(first_payload, first=0, count=32, key="quota-first")
            await repository.put_batch(
                meeting_id,
                first_capture.capture.id,
                first_capture.capture_token,
                1,
                0,
                first_metadata,
                _stream(first_payload),
            )
            await repository.put_batch(
                second_meeting.id,
                second_capture.capture.id,
                second_capture.capture_token,
                1,
                0,
                _metadata(second_payload, first=0, count=16, key="quota-second"),
                _stream(second_payload),
            )

            replay = await repository.put_batch(
                meeting_id,
                first_capture.capture.id,
                first_capture.capture_token,
                1,
                0,
                first_metadata.model_copy(update={"idempotency_key": "quota-first-replay"}),
                _stream(first_payload),
            )
            assert replay.replayed is True

            extra = b"\x03\x00" * 16
            with pytest.raises(MeetingNativeCaptureTooLarge) as full:
                await repository.put_batch(
                    second_meeting.id,
                    second_capture.capture.id,
                    second_capture.capture_token,
                    1,
                    1,
                    _metadata(extra, first=16, count=16, key="quota-overflow"),
                    _stream(extra),
                )
            assert full.value.code == "NATIVE_CAPTURE_OWNER_RETAINED_BYTES_LIMIT"
            captures = list((await session.exec(select(MeetingNativeCapture))).all())
            assert sum(value.total_bytes for value in captures) == 96
            assert len((await session.exec(select(MeetingNativeCaptureBatch))).all()) == 2
        await engine.dispose()

    anyio.run(scenario)


@pytest.mark.parametrize("failure_call", [1, 2, 3, 4, 5])
def test_native_capture_directory_fsync_failure_is_not_acked(tmp_path, monkeypatch, failure_call):
    async def scenario():
        engine, factory = await _database()
        meeting_id, _ = await _seed(factory)
        native = _native_settings(tmp_path)
        storage = MeetingNativeCaptureStorage(native.root)
        async with factory() as session:
            repository = MeetingNativeCaptureRepository(
                session,
                storage,
                native,
                meeting_settings=_meeting_settings(),
            )
            created = await repository.create(
                meeting_id,
                7,
                NativeCaptureCreateRequest(device_installation_id="device-installation-fsync"),
                idempotency_key="directory-fsync-capture",
            )
            payload = b"\x01\x00" * 16
            metadata = _metadata(payload, first=0, count=16, key="directory-fsync-batch")
            original_fsync = MeetingNativeCaptureStorage._fsync_directory
            fsync_paths = []

            def fail_directory_fsync(path):
                fsync_paths.append(path)
                if len(fsync_paths) == failure_call:
                    raise OSError("injected directory fsync failure")
                original_fsync(path)

            with monkeypatch.context() as patch:
                patch.setattr(
                    MeetingNativeCaptureStorage,
                    "_fsync_directory",
                    staticmethod(fail_directory_fsync),
                )
                with pytest.raises(MeetingNativeCaptureUnavailable) as unavailable:
                    await repository.put_batch(
                        meeting_id,
                        created.capture.id,
                        created.capture_token,
                        1,
                        0,
                        metadata,
                        _stream(payload),
                    )
                assert unavailable.value.code == "NATIVE_CAPTURE_STORAGE_UNAVAILABLE"
                assert (await session.exec(select(MeetingNativeCaptureBatch))).first() is None
                capture = (await session.exec(select(MeetingNativeCapture))).one()
                assert capture.total_bytes == 0
                expected_paths = [
                    native.root,
                    native.root / "7",
                    native.root / "7" / created.capture.id,
                    native.root / "7" / created.capture.id / "1",
                    native.root / "7" / created.capture.id / "1",
                ]
                expected_call_count = 5 if failure_call == 4 else failure_call
                assert fsync_paths == expected_paths[:expected_call_count]

            retry = await repository.put_batch(
                meeting_id,
                created.capture.id,
                created.capture_token,
                1,
                0,
                metadata,
                _stream(payload),
            )
            assert retry.replayed is False
            assert (await session.exec(select(MeetingNativeCaptureBatch))).one().byte_size == len(payload)
            epoch_root = native.root / "7" / created.capture.id / "1"
            assert list(epoch_root.glob(".*.tmp")) == []
        await engine.dispose()

    anyio.run(scenario)


def test_sealed_capture_rejects_manifest_revision_drift(tmp_path):
    async def scenario():
        engine, factory = await _database()
        meeting_id, _ = await _seed(factory)
        native = _native_settings(tmp_path)
        async with factory() as session:
            repository = MeetingNativeCaptureRepository(
                session,
                MeetingNativeCaptureStorage(native.root),
                native,
                meeting_settings=_meeting_settings(),
            )
            created = await repository.create(
                meeting_id,
                7,
                NativeCaptureCreateRequest(device_installation_id="device-installation-revision"),
                idempotency_key="native-revision-start",
            )
            payload = b"\x01\x00" * 16
            await repository.seal(
                meeting_id,
                created.capture.id,
                created.capture_token,
                _boundary(
                    1,
                    0,
                    16,
                    revision=2,
                    entries=[_manifest_entry(payload, first=0, count=16, sequence=0)],
                ),
            )
            with pytest.raises(MeetingNativeCaptureConflict) as drift:
                await repository.put_batch(
                    meeting_id,
                    created.capture.id,
                    created.capture_token,
                    1,
                    0,
                    _metadata(payload, first=0, count=16, key="revision-drift", revision=3),
                    _stream(payload),
                )
            assert drift.value.code == "NATIVE_CAPTURE_MANIFEST_REVISION_CONFLICT"
            changed = b"\x02\x00" * 16
            with pytest.raises(MeetingNativeCaptureConflict) as swapped:
                await repository.put_batch(
                    meeting_id,
                    created.capture.id,
                    created.capture_token,
                    1,
                    0,
                    _metadata(changed, first=0, count=16, key="content-swap", revision=2),
                    _stream(changed),
                )
            assert swapped.value.code == "NATIVE_CAPTURE_MANIFEST_ENTRY_CONFLICT"
        await engine.dispose()

    anyio.run(scenario)


def test_manifest_revision_is_bounded_to_database_integer():
    payload = b"\x00\x00" * 16
    with pytest.raises(ValueError):
        _metadata(
            payload,
            first=0,
            count=16,
            key="revision-overflow",
            revision=2_147_483_648,
        )
    with pytest.raises(ValueError):
        _boundary(1, 0, 16, revision=2_147_483_648)


def test_manifest_digest_is_recomputed_from_canonical_entries():
    entry = _manifest_entry(b"\x00\x00" * 16, first=0, count=16, sequence=0)
    with pytest.raises(ValueError, match="digest"):
        NativeCaptureBoundaryRequest(
            expected_epoch=1,
            final_sequence=0,
            recorded_through_sample=16,
            manifest_revision=2,
            manifest_sha256="0" * 64,
            manifest_entries=[entry],
        )


def test_manifest_digest_is_independent_of_entry_transport_order():
    first = _manifest_entry(b"\x01\x00" * 16, first=0, count=16, sequence=0)
    second = _manifest_entry(b"\x02\x00" * 16, first=16, count=16, sequence=1)
    arguments = {
        "expected_epoch": 1,
        "final_sequence": 1,
        "recorded_through_sample": 32,
        "manifest_revision": 2,
    }
    assert native_capture_manifest_sha256(**arguments, entries=[first, second]) == (
        native_capture_manifest_sha256(**arguments, entries=[second, first])
    )


def test_manifest_digest_matches_ios_golden_vector():
    entry = NativeCaptureManifestEntry(
        sequence=0,
        first_sample=0,
        sample_count=16_000,
        captured_monotonic_ns=1,
        encoding="pcm_s16le",
        sample_rate=16_000,
        channels=1,
        sha256=hashlib.sha256(bytes([1]) * 32_000).hexdigest(),
    )
    assert native_capture_manifest_sha256(
        expected_epoch=1,
        final_sequence=0,
        recorded_through_sample=16_000,
        manifest_revision=3,
        entries=[entry],
    ) == "9abc5bec51abd3bccf0074243c26a4096f487b3b96875cf669d2053bb9e74c58"


def test_native_capture_rollover_expires_old_lease_and_uses_existing_stream_ticket(tmp_path):
    async def scenario():
        engine, factory = await _database()
        meeting_id, _ = await _seed(factory)
        native = _native_settings(tmp_path)
        meeting_settings = _meeting_settings()
        async with factory() as session:
            repository = MeetingNativeCaptureRepository(
                session,
                MeetingNativeCaptureStorage(native.root),
                native,
                meeting_settings=meeting_settings,
            )
            created = await repository.create(
                meeting_id,
                7,
                NativeCaptureCreateRequest(device_installation_id="device-installation-0002"),
                idempotency_key="native-start-rollover",
            )
            session.add(
                MeetingStreamLease(
                    meeting_id=meeting_id,
                    stream_epoch=1,
                    connection_id="old-websocket",
                    owner_user_id=7,
                    lease_until=utcnow() + timedelta(minutes=5),
                )
            )
            await session.commit()
            raw_stream, ticket, capture, checkpoint, replayed = await repository.rollover(
                meeting_id,
                created.capture.id,
                7,
                _boundary(1, -1, 0),
                idempotency_key="rollover-1",
                origin="https://siq.example",
            )
            assert replayed is False
            assert capture.current_epoch == created.capture.current_epoch + 1
            assert capture.current_epoch == ticket.stream_epoch == 2
            assert checkpoint.realtime_checkpoint.stream_epoch == 2
            lease = (await session.exec(select(MeetingStreamLease))).one()
            assert lease.lease_until <= utcnow()

            consumed, meeting = await MeetingStreamTicketService(session, meeting_settings).consume(
                meeting_id,
                raw_stream,
                origin="https://siq.example",
                connection_id="native-rollover",
            )
            assert consumed.stream_epoch == meeting.stream_epoch == 2

            replay_raw, replay_ticket, _, _, replayed = await repository.rollover(
                meeting_id,
                created.capture.id,
                7,
                _boundary(1, -1, 0),
                idempotency_key="rollover-1",
                origin="https://siq.example",
            )
            assert replayed is True
            assert replay_raw != raw_stream
            assert replay_ticket.stream_epoch == 2
            latest_raw, latest_ticket, _, _, replayed = await repository.rollover(
                meeting_id,
                created.capture.id,
                7,
                _boundary(1, -1, 0),
                idempotency_key="rollover-1",
                origin="https://siq.example",
            )
            assert replayed is True
            assert latest_raw != replay_raw
            assert latest_ticket.stream_epoch == 2
            unconsumed_tickets = list(
                (
                    await session.exec(
                        select(MeetingStreamTicket).where(
                            MeetingStreamTicket.meeting_id == meeting_id,
                            MeetingStreamTicket.stream_epoch == 2,
                            MeetingStreamTicket.purpose == "meeting_audio_producer",
                            MeetingStreamTicket.consumed_at.is_(None),
                        )
                    )
                ).all()
            )
            assert [value.id for value in unconsumed_tickets] == [latest_ticket.id]
            with pytest.raises(StreamTicketError):
                await MeetingStreamTicketService(session, meeting_settings).consume(
                    meeting_id,
                    replay_raw,
                    origin="https://siq.example",
                    connection_id="superseded-rollover-ticket",
                )
            await MeetingStreamTicketService(session, meeting_settings).release_lease(
                meeting_id,
                "native-rollover",
            )
            consumed_latest, _ = await MeetingStreamTicketService(session, meeting_settings).consume(
                meeting_id,
                latest_raw,
                origin="https://siq.example",
                connection_id="latest-rollover-ticket",
            )
            assert consumed_latest.id == latest_ticket.id
            with pytest.raises(MeetingNativeCaptureConflict) as changed:
                await repository.rollover(
                    meeting_id,
                    created.capture.id,
                    7,
                    _boundary(
                        1,
                        0,
                        16,
                        entries=[
                            _manifest_entry(
                                b"\x00\x00" * 16,
                                first=0,
                                count=16,
                                sequence=0,
                            )
                        ],
                    ),
                    idempotency_key="rollover-1",
                    origin="https://siq.example",
                )
            assert changed.value.code == "NATIVE_CAPTURE_IDEMPOTENCY_CONFLICT"
        await engine.dispose()

    anyio.run(scenario)


@pytest.mark.parametrize("failure_call", [1, 2, 3])
def test_native_capture_storage_root_chain_fsync_is_fail_closed_and_recoverable(
    tmp_path,
    monkeypatch,
    failure_call,
):
    intermediate = tmp_path / "new-native-parent"
    root = intermediate / "native-root"
    original_fsync = MeetingNativeCaptureStorage._fsync_directory
    fsync_paths = []

    def fail_directory_fsync(path):
        fsync_paths.append(path)
        if len(fsync_paths) == failure_call:
            raise OSError("injected root-chain fsync failure")
        original_fsync(path)

    with monkeypatch.context() as patch:
        patch.setattr(
            MeetingNativeCaptureStorage,
            "_fsync_directory",
            staticmethod(fail_directory_fsync),
        )
        with pytest.raises(MeetingNativeCaptureStorageError) as unavailable:
            MeetingNativeCaptureStorage(root)
    assert unavailable.value.code == "NATIVE_CAPTURE_STORAGE_UNAVAILABLE"
    expected_initial = [tmp_path.parent, tmp_path, intermediate]
    assert fsync_paths == expected_initial[:failure_call]

    recovery_paths = []

    def record_recovery_fsync(path):
        recovery_paths.append(path)
        original_fsync(path)

    with monkeypatch.context() as patch:
        patch.setattr(
            MeetingNativeCaptureStorage,
            "_fsync_directory",
            staticmethod(record_recovery_fsync),
        )
        storage = MeetingNativeCaptureStorage(root)
    expected_recovery = {
        1: [tmp_path.parent, tmp_path, intermediate],
        2: [tmp_path, intermediate],
        3: [intermediate],
    }
    assert recovery_paths == expected_recovery[failure_call]
    assert storage.root == root
    assert root.is_dir()


def test_native_capture_storage_rejects_escape_and_symlink(tmp_path):
    storage = MeetingNativeCaptureStorage(tmp_path / "native")
    with pytest.raises(MeetingNativeCaptureStorageError):
        storage.resolve_storage_key("../outside.pcm")
    outside = tmp_path / "outside"
    outside.mkdir()
    (storage.root / "7").symlink_to(outside, target_is_directory=True)

    async def persist_through_symlink():
        await storage.persist_batch(
            7,
            "capture-safe-id",
            1,
            0,
            _stream(b"\x00\x00"),
            expected_size=2,
            expected_sha256=hashlib.sha256(b"\x00\x00").hexdigest(),
            max_bytes=64,
        )

    with pytest.raises(MeetingNativeCaptureStorageError):
        anyio.run(persist_through_symlink)
    assert list(outside.iterdir()) == []
    real_root = tmp_path / "real-native-root"
    real_root.mkdir()
    linked_root = tmp_path / "linked-native-root"
    linked_root.symlink_to(real_root, target_is_directory=True)
    with pytest.raises(MeetingNativeCaptureStorageError):
        MeetingNativeCaptureStorage(linked_root)


def test_native_capture_storage_preserves_free_space_reserve(tmp_path, monkeypatch):
    storage = MeetingNativeCaptureStorage(tmp_path / "native-low-space")
    monkeypatch.setattr(
        "services.meeting_native_capture_storage.shutil.disk_usage",
        lambda _path: type("DiskUsage", (), {"free": 1})(),
    )

    async def persist():
        await storage.persist_batch(
            7,
            "capture-low-space",
            1,
            0,
            _stream(b"\x00\x00" * 16),
            expected_size=32,
            expected_sha256=hashlib.sha256(b"\x00\x00" * 16).hexdigest(),
            max_bytes=64,
            min_free_bytes=16,
        )

    with pytest.raises(MeetingNativeCaptureStorageError) as low_space:
        anyio.run(persist)
    assert low_space.value.code == "NATIVE_CAPTURE_STORAGE_LOW_SPACE"


def test_native_capture_storage_fsyncs_batch_parent_after_remove(tmp_path, monkeypatch):
    storage = MeetingNativeCaptureStorage(tmp_path / "native-remove-fsync")
    payload = b"\x00\x00" * 16

    async def persist(sequence):
        return await storage.persist_batch(
            7,
            "capture-remove-fsync",
            1,
            sequence,
            _stream(payload),
            expected_size=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
            max_bytes=64,
        )

    first = anyio.run(persist, 0)
    first_path = storage.resolve_storage_key(first.storage_key)
    original_fsync = MeetingNativeCaptureStorage._fsync_directory
    fsync_paths = []

    def record_fsync(path):
        fsync_paths.append(path)
        original_fsync(path)

    with monkeypatch.context() as patch:
        patch.setattr(MeetingNativeCaptureStorage, "_fsync_directory", staticmethod(record_fsync))
        storage.remove_if_created(first)
    assert not first_path.exists()
    assert fsync_paths == [first_path.parent]

    second = anyio.run(persist, 1)
    second_path = storage.resolve_storage_key(second.storage_key)

    def fail_fsync(_path):
        raise OSError("injected remove fsync failure")

    with monkeypatch.context() as patch:
        patch.setattr(MeetingNativeCaptureStorage, "_fsync_directory", staticmethod(fail_fsync))
        with pytest.raises(MeetingNativeCaptureStorageError) as unavailable:
            storage.remove_if_created(second)
    assert unavailable.value.code == "NATIVE_CAPTURE_STORAGE_UNAVAILABLE"
    assert not second_path.exists()


def test_native_capture_missing_ranges_do_not_expand_declared_sequence_space():
    assert _missing_integer_ranges({0, 2, 1_000_000_000_000}, 1_000_000_000_000) == [
        {"start": 1, "end": 1},
        {"start": 3, "end": 999_999_999_999},
    ]


def test_native_capture_router_requires_header_token_and_hides_cross_owner(tmp_path, monkeypatch):
    engine, factory = anyio.run(_database)
    meeting_id, other_meeting_id = anyio.run(_seed, factory)
    native = _native_settings(tmp_path)
    meeting_settings = _meeting_settings()
    monkeypatch.setattr(
        "routers.meeting_native_captures._require_enabled",
        lambda: (native, meeting_settings),
    )
    active_user = {
        "value": User(
            id=7,
            username="native-route-owner",
            email="native-route-owner@example.test",
            hashed_password="x",
            full_name="Native Route Owner",
            role=UserRole.ANALYST,
            is_active=True,
        )
    }

    async def current_user():
        return active_user["value"]

    async def session_dependency():
        async with factory() as session:
            yield session

    app = FastAPI()
    app.include_router(native_router, prefix="/api")
    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_meeting_async_session] = session_dependency
    client = TestClient(app)
    created = client.post(
        f"/api/meetings/v1/sessions/{meeting_id}/native-captures",
        headers={"Idempotency-Key": "route-native-start"},
        json={"device_installation_id": "device-installation-route-1"},
    )
    assert created.status_code == 201
    capture_id = created.json()["capture"]["id"]
    token = created.json()["capture_token"]
    device_header = {"X-SIQ-Device-Installation-Id": "device-installation-route-1"}
    token_headers = {"Authorization": f"Bearer {token}", **device_header}
    checkpoint_path = f"/api/meetings/v1/sessions/{meeting_id}/native-captures/{capture_id}/checkpoint"
    assert client.get(checkpoint_path, headers=device_header, params={"capture_token": token}).status_code == 401
    assert client.get(checkpoint_path, headers=token_headers).status_code == 200
    assert (
        client.get(
            checkpoint_path,
            headers={
                "Authorization": f"Bearer {token}",
                "X-SIQ-Device-Installation-Id": "device-installation-wrong",
            },
        ).status_code
        == 401
    )
    assert (
        client.get(
            f"/api/meetings/v1/sessions/{other_meeting_id}/native-captures/{capture_id}/checkpoint",
            headers=token_headers,
        ).status_code
        == 404
    )

    payload = b"\x01\x00" * 16
    invalid_metadata = client.put(
        f"/api/meetings/v1/sessions/{meeting_id}/native-captures/{capture_id}/batches/1/0",
        headers={
            **token_headers,
            "Idempotency-Key": "route-batch-invalid",
            "Content-Type": "application/octet-stream",
            "X-SIQ-First-Sample": "0",
            "X-SIQ-Sample-Count": "16",
            "X-SIQ-Captured-Monotonic-Ns": "1000000000",
            "X-SIQ-Audio-Encoding": "aac",
            "X-SIQ-Sample-Rate": "16000",
            "X-SIQ-Channels": "1",
            "X-SIQ-SHA256": hashlib.sha256(payload).hexdigest(),
            "X-SIQ-Manifest-Revision": "1",
        },
        content=payload,
    )
    assert invalid_metadata.status_code == 400
    assert invalid_metadata.json()["detail"]["code"] == "NATIVE_CAPTURE_BATCH_METADATA_INVALID"
    uploaded = client.put(
        f"/api/meetings/v1/sessions/{meeting_id}/native-captures/{capture_id}/batches/1/0",
        headers={
            **token_headers,
            "Idempotency-Key": "route-batch-0",
            "Content-Type": "application/octet-stream",
            "X-SIQ-First-Sample": "0",
            "X-SIQ-Sample-Count": "16",
            "X-SIQ-Captured-Monotonic-Ns": "1000000000",
            "X-SIQ-Audio-Encoding": "pcm_s16le",
            "X-SIQ-Sample-Rate": "16000",
            "X-SIQ-Channels": "1",
            "X-SIQ-SHA256": hashlib.sha256(payload).hexdigest(),
            "X-SIQ-Manifest-Revision": "1",
        },
        content=payload,
    )
    assert uploaded.status_code == 200
    assert uploaded.json()["replayed"] is False
    replay = client.put(
        f"/api/meetings/v1/sessions/{meeting_id}/native-captures/{capture_id}/batches/1/0",
        headers={
            **token_headers,
            "Idempotency-Key": "route-batch-0",
            "Content-Type": "application/octet-stream",
            "X-SIQ-First-Sample": "0",
            "X-SIQ-Sample-Count": "16",
            "X-SIQ-Captured-Monotonic-Ns": "1000000000",
            "X-SIQ-Audio-Encoding": "pcm_s16le",
            "X-SIQ-Sample-Rate": "16000",
            "X-SIQ-Channels": "1",
            "X-SIQ-SHA256": hashlib.sha256(payload).hexdigest(),
            "X-SIQ-Manifest-Revision": "1",
        },
        content=payload,
    )
    assert replay.status_code == 200
    assert replay.json()["replayed"] is True

    sealed = client.post(
        f"/api/meetings/v1/sessions/{meeting_id}/native-captures/{capture_id}/seal",
        headers=token_headers,
        json=_boundary(
            1,
            1,
            32,
            entries=[
                _manifest_entry(payload, first=0, count=16, sequence=0),
                _manifest_entry(b"\x00\x00" * 16, first=16, count=16, sequence=1),
            ],
        ).model_dump(mode="json"),
    )
    assert sealed.status_code == 200
    gap_path = f"/api/meetings/v1/sessions/{meeting_id}/native-captures/{capture_id}/gaps"
    gap_payload = {
        "stream_epoch": 1,
        "from_sequence": 1,
        "to_sequence": 1,
        "start_sample": 16,
        "end_sample": 32,
        "reason": "file_corrupt",
        "manifest_revision": 2,
    }
    assert (
        client.post(
            f"/api/meetings/v1/sessions/{other_meeting_id}/native-captures/{capture_id}/gaps",
            headers={"Idempotency-Key": "route-gap-other-meeting"},
            json=gap_payload,
        ).status_code
        == 404
    )
    gap = client.post(
        gap_path,
        headers={"Idempotency-Key": "route-gap-1"},
        json=gap_payload,
    )
    assert gap.status_code == 200
    assert gap.json()["checkpoint"]["finalization_checkpoint"]["packaging_state"] == "queued"

    active_user["value"] = active_user["value"].model_copy(
        update={"id": 8, "username": "native-route-other", "email": "native-route-other@example.test"}
    )
    hidden = client.post(f"/api/meetings/v1/sessions/{meeting_id}/native-captures/{capture_id}/token")
    assert hidden.status_code == 404
    active_user["value"] = active_user["value"].model_copy(
        update={"id": 7, "username": "native-route-owner", "email": "native-route-owner@example.test"}
    )
    revoked = client.post(f"/api/meetings/v1/sessions/{meeting_id}/native-captures/{capture_id}/token/revoke")
    assert revoked.status_code == 200
    assert client.get(checkpoint_path, headers=token_headers).status_code == 401
    anyio.run(engine.dispose)


def test_native_capture_capability_is_default_off_and_never_claims_web_background_support(monkeypatch):
    monkeypatch.delenv("SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED", raising=False)
    disabled = meeting_capabilities(MeetingSettings(enabled=True))
    native = disabled["audio"]["capture_adapters"]["ios_native"]
    assert native["available"] is False
    assert native["web_background_recording_supported"] is False

    monkeypatch.setenv("SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED", "1")
    enabled = meeting_capabilities(MeetingSettings(enabled=True))
    native = enabled["audio"]["capture_adapters"]["ios_native"]
    assert native["available"] is True
    assert native["requires_native_runtime"] is True
    assert native["limits"]["max_retained_bytes_per_owner"] >= native["limits"]["max_total_bytes"]


def test_native_capture_rejects_owner_quota_below_single_capture_limit(monkeypatch):
    monkeypatch.setenv("SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED", "1")
    monkeypatch.setenv("SIQ_MEETING_NATIVE_CAPTURE_MAX_TOTAL_BYTES", "6400")
    monkeypatch.setenv("SIQ_MEETING_NATIVE_CAPTURE_MAX_RETAINED_BYTES_PER_OWNER", "3200")
    settings = MeetingNativeCaptureSettings.from_env()
    assert settings.operational is False
    assert any("MAX_RETAINED_BYTES_PER_OWNER" in error for error in settings.errors)
