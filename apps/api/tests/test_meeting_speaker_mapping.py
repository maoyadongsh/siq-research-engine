# ruff: noqa: B008

from __future__ import annotations

import json

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from routers.meetings import router
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole
from services.meeting_contracts import (
    MEETING_TABLES,
    MeetingEvent,
    MeetingIdempotencyRecord,
    MeetingSession,
    MeetingSpeakerTrack,
    MeetingTranscriptSegment,
    SegmentSpeakerRenameRequest,
    SpeakerMergeRequest,
    SpeakerSplitRequest,
)
from services.meeting_database import get_meeting_async_session as get_async_session
from services.meeting_event_store import decode_json
from services.meeting_repository import (
    MeetingInvalidOperation,
    MeetingRepository,
    MeetingResourceNotFound,
    MeetingVersionConflict,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


def _user(user_id: int, role: UserRole = UserRole.ANALYST) -> User:
    return User(
        id=user_id,
        username=f"speaker-user-{user_id}",
        email=f"speaker-user-{user_id}@example.test",
        hashed_password="x",
        full_name=f"Speaker User {user_id}",
        role=role,
        is_active=True,
        approval_status="approved",
    )


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
                tables=[User.__table__, *[model.__table__ for model in MEETING_TABLES]],
            )
        )
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _seed(session: AsyncSession, *, state: str = "stopped"):
    meeting = MeetingSession(owner_user_id=7, title="speaker mapping", state=state)
    session.add(meeting)
    await session.flush()
    target = MeetingSpeakerTrack(
        meeting_id=meeting.id,
        track_key="speaker-target",
        anonymous_label="发言人 1",
        display_name="人工目标姓名",
        label_source="manual",
    )
    source = MeetingSpeakerTrack(
        meeting_id=meeting.id,
        track_key="speaker-source",
        anonymous_label="发言人 2",
        display_name="原人工姓名",
        label_source="manual",
    )
    session.add_all([target, source])
    await session.flush()
    segments = []
    for ordinal, track in enumerate((target, source, source), start=1):
        segment = MeetingTranscriptSegment(
            meeting_id=meeting.id,
            ordinal=ordinal,
            utterance_id=f"u-{ordinal}",
            provider_segment_key=f"p-{ordinal}",
            start_ms=(ordinal - 1) * 1_000,
            end_ms=ordinal * 1_000,
            speaker_track_id=track.id,
            raw_text=f"敏感正文 {ordinal}",
            asr_final_text=f"敏感正文 {ordinal}",
            asr_provider="funasr",
            asr_model="paraformer",
            asr_version="v1",
        )
        session.add(segment)
        segments.append(segment)
    await session.commit()
    return meeting, target, source, segments


def test_repository_merge_split_emit_mappings_and_preserve_names():
    async def scenario():
        engine, factory = await _database()
        async with factory() as session:
            meeting, target, source, segments = await _seed(session)
            repository = MeetingRepository(session)
            merged = await repository.merge_speakers(
                meeting.id,
                target.id,
                7,
                SpeakerMergeRequest(
                    source_track_ids=[source.id],
                    expected_versions={target.id: 1, source.id: 1},
                ),
            )
            assert merged.operation == "merge"
            assert merged.source_track_ids == [source.id]
            assert set(merged.segment_ids) == {segments[1].id, segments[2].id}
            assert merged.tracks[0].display_name == "人工目标姓名"
            assert merged.tracks[1].display_name == "原人工姓名"

            with pytest.raises(MeetingVersionConflict):
                await repository.split_speaker(
                    meeting.id,
                    target.id,
                    7,
                    SpeakerSplitRequest(
                        segment_ids=[segments[2].id],
                        expected_version=1,
                    ),
                )
            split = await repository.split_speaker(
                meeting.id,
                target.id,
                7,
                SpeakerSplitRequest(
                    segment_ids=[segments[2].id],
                    expected_version=2,
                    display_name="拆分后的姓名",
                ),
            )
            assert split.operation == "split"
            assert split.source_track_ids == [target.id]
            assert split.segment_ids == [segments[2].id]
            assert split.tracks[0].display_name == "人工目标姓名"
            assert split.tracks[1].display_name == "拆分后的姓名"

            events = list((await session.exec(select(MeetingEvent))).all())
            mapping_events = [
                value for value in events if value.event_type in {"speaker.track.merged", "speaker.track.split"}
            ]
            assert len(mapping_events) == 2
            for event in mapping_events:
                payload = decode_json(event.payload_json, {})
                assert payload["automatic"] is False
                assert "敏感正文" not in event.payload_json
        await engine.dispose()

    anyio.run(scenario)


def test_repository_speaker_mapping_requires_owner_and_post_meeting_state():
    async def scenario():
        engine, factory = await _database()
        async with factory() as session:
            meeting, target, source, _ = await _seed(session, state="live")
            repository = MeetingRepository(session)
            request = SpeakerMergeRequest(
                source_track_ids=[source.id],
                expected_versions={target.id: 1, source.id: 1},
            )
            with pytest.raises(MeetingResourceNotFound):
                await repository.merge_speakers(meeting.id, target.id, 8, request)
            with pytest.raises(MeetingInvalidOperation):
                await repository.merge_speakers(meeting.id, target.id, 7, request)
        await engine.dispose()

    anyio.run(scenario)


def test_repository_renames_one_segment_without_rewriting_same_speaker_segments():
    async def scenario():
        engine, factory = await _database()
        async with factory() as session:
            meeting, _, source, segments = await _seed(session)
            result = await MeetingRepository(session).rename_segment_speaker(
                meeting.id,
                segments[1].id,
                7,
                SegmentSpeakerRenameRequest(
                    display_name="王敏",
                    scope="segment",
                    expected_speaker_version=1,
                ),
            )

            assert result.operation == "rename_segment"
            assert result.scope == "segment"
            assert result.affected_segment_count == 1
            assert result.segment.id == segments[1].id
            assert result.segment.speaker_label == "王敏"
            assert result.segment.speaker_track_id != source.id
            assert len(result.tracks) == 2
            assert result.tracks[0].id == source.id
            assert result.tracks[0].display_name == "原人工姓名"
            assert result.tracks[0].version == 2
            assert result.tracks[1].display_name == "王敏"

            persisted = list(
                (
                    await session.exec(
                        select(MeetingTranscriptSegment)
                        .where(MeetingTranscriptSegment.meeting_id == meeting.id)
                        .order_by(MeetingTranscriptSegment.ordinal)
                    )
                ).all()
            )
            assert persisted[1].speaker_track_id == result.tracks[1].id
            assert persisted[2].speaker_track_id == source.id
            event = (
                await session.exec(
                    select(MeetingEvent).where(MeetingEvent.event_id == result.event_id)
                )
            ).one()
            payload = decode_json(event.payload_json, {})
            assert payload["reason"] == "manual_segment_speaker_rename"
            assert "敏感正文" not in event.payload_json
        await engine.dispose()

    anyio.run(scenario)


def test_repository_renames_all_segments_and_collapses_single_segment_scope_safely():
    async def scenario():
        engine, factory = await _database()
        async with factory() as session:
            meeting, target, source, segments = await _seed(session)
            repository = MeetingRepository(session)
            all_result = await repository.rename_segment_speaker(
                meeting.id,
                segments[1].id,
                7,
                SegmentSpeakerRenameRequest(
                    display_name="李然",
                    scope="speaker",
                    expected_speaker_version=1,
                ),
            )
            assert all_result.operation == "rename_speaker"
            assert all_result.affected_segment_count == 2
            assert all_result.segment.speaker_track_id == source.id
            assert all_result.segment.speaker_label == "李然"
            assert all_result.tracks[0].version == 2

            single_result = await repository.rename_segment_speaker(
                meeting.id,
                segments[0].id,
                7,
                SegmentSpeakerRenameRequest(
                    display_name="周宁",
                    scope="segment",
                    expected_speaker_version=1,
                ),
            )
            assert single_result.operation == "rename_segment"
            assert single_result.affected_segment_count == 1
            assert single_result.segment.speaker_track_id == target.id
            assert single_result.tracks[0].id == target.id
            assert single_result.tracks[0].display_name == "周宁"
            tracks = list(
                (
                    await session.exec(
                        select(MeetingSpeakerTrack).where(
                            MeetingSpeakerTrack.meeting_id == meeting.id
                        )
                    )
                ).all()
            )
            assert len(tracks) == 2
        await engine.dispose()

    anyio.run(scenario)


def test_router_merge_split_enforce_bola_permission_and_conflict(monkeypatch):
    async def scenario():
        engine, factory = await _database()
        async with factory() as session:
            meeting, target, source, segments = await _seed(session)
        return engine, factory, meeting, target, source, segments

    engine, factory, meeting, target, source, segments = anyio.run(scenario)
    active_user = {"value": _user(8)}
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def current_user():
        return active_user["value"]

    async def session_dependency():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_async_session] = session_dependency
    monkeypatch.setenv("SIQ_MEETINGS_ENABLED", "true")
    monkeypatch.setenv("SIQ_MEETINGS_ASR_ENABLED", "false")
    client = TestClient(app)
    merge_url = f"/api/meetings/v1/sessions/{meeting.id}/speakers/{target.id}/merge"
    merge_body = {
        "source_track_ids": [source.id],
        "expected_versions": {target.id: 1, source.id: 1},
    }

    hidden = client.post(merge_url, json=merge_body)
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "MEETING_RESOURCE_NOT_FOUND"

    active_user["value"] = _user(7, UserRole.VIEWER)
    denied = client.post(merge_url, json=merge_body)
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "MEETING_PERMISSION_DENIED"

    active_user["value"] = _user(7)
    stale = client.post(
        merge_url,
        json={
            **merge_body,
            "expected_versions": {target.id: 2, source.id: 1},
        },
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "MEETING_VERSION_CONFLICT"

    merged = client.post(
        merge_url,
        json=merge_body,
        headers={"Idempotency-Key": "speaker-merge-1"},
    )
    assert merged.status_code == 200
    assert merged.json()["operation"] == "merge"
    replayed_merge = client.post(
        merge_url,
        json=merge_body,
        headers={"Idempotency-Key": "speaker-merge-1"},
    )
    assert replayed_merge.status_code == 200
    assert replayed_merge.json() == merged.json()
    split = client.post(
        f"/api/meetings/v1/sessions/{meeting.id}/speakers/{target.id}/split",
        json={
            "segment_ids": [segments[2].id],
            "expected_version": 2,
        },
    )
    assert split.status_code == 200
    assert split.json()["operation"] == "split"
    assert split.json()["event_cursor"] > merged.json()["event_cursor"]
    listed = client.get(f"/api/meetings/v1/sessions/{meeting.id}/speakers")
    assert listed.status_code == 200
    listed_ids = {item["id"] for item in listed.json()}
    assert source.id not in listed_ids
    assert target.id in listed_ids
    anyio.run(engine.dispose)


def test_router_speaker_rename_replays_exact_snapshots_after_later_changes(monkeypatch):
    async def scenario():
        engine, factory = await _database()
        async with factory() as session:
            meeting, target, source, segments = await _seed(session)
        return engine, factory, meeting, target, source, segments

    engine, factory, meeting, target, source, segments = anyio.run(scenario)
    active_user = {"value": _user(8)}
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def current_user():
        return active_user["value"]

    async def session_dependency():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_async_session] = session_dependency
    monkeypatch.setenv("SIQ_MEETINGS_ENABLED", "true")
    monkeypatch.setenv("SIQ_MEETINGS_ASR_ENABLED", "false")
    client = TestClient(app)
    url = f"/api/meetings/v1/sessions/{meeting.id}/segments/{segments[1].id}/speaker"
    body = {
        "display_name": "陈晓",
        "scope": "segment",
        "expected_speaker_version": source.version,
    }

    hidden = client.patch(url, json=body)
    assert hidden.status_code == 404
    assert hidden.json()["detail"]["code"] == "MEETING_RESOURCE_NOT_FOUND"

    active_user["value"] = _user(7, UserRole.VIEWER)
    denied = client.patch(url, json=body)
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "MEETING_PERMISSION_DENIED"

    active_user["value"] = _user(7)
    stale = client.patch(url, json={**body, "expected_speaker_version": 9})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "MEETING_VERSION_CONFLICT"

    speaker_url = f"/api/meetings/v1/sessions/{meeting.id}/speakers/{target.id}"
    speaker_body = {"display_name": "首次全局姓名", "expected_version": target.version}
    first_speaker_rename = client.patch(
        speaker_url,
        json=speaker_body,
        headers={"Idempotency-Key": "speaker-rename-snapshot-1"},
    )
    assert first_speaker_rename.status_code == 200
    first_speaker_payload = first_speaker_rename.json()
    later_speaker_rename = client.patch(
        speaker_url,
        json={
            "display_name": "后续全局姓名",
            "expected_version": first_speaker_payload["version"],
        },
        headers={"Idempotency-Key": "speaker-rename-snapshot-2"},
    )
    assert later_speaker_rename.status_code == 200
    assert later_speaker_rename.json()["display_name"] == "后续全局姓名"
    replayed_speaker_rename = client.patch(
        speaker_url,
        json=speaker_body,
        headers={"Idempotency-Key": "speaker-rename-snapshot-1"},
    )
    assert replayed_speaker_rename.status_code == 200
    assert replayed_speaker_rename.json() == first_speaker_payload

    renamed = client.patch(url, json=body, headers={"Idempotency-Key": "segment-speaker-rename-1"})
    assert renamed.status_code == 200
    payload = renamed.json()
    assert payload["operation"] == "rename_segment"
    assert payload["scope"] == "segment"
    assert payload["affected_segment_count"] == 1
    assert payload["segment"]["speaker_label"] == "陈晓"
    assert payload["segment"]["speaker_track_id"] != source.id
    assert payload["event_cursor"] > 0

    later_segment_rename = client.patch(
        url,
        json={
            "display_name": "后续单段姓名",
            "scope": "speaker",
            "expected_speaker_version": payload["tracks"][-1]["version"],
        },
        headers={"Idempotency-Key": "segment-speaker-rename-2"},
    )
    assert later_segment_rename.status_code == 200
    assert later_segment_rename.json()["segment"]["speaker_label"] == "后续单段姓名"

    replayed = client.patch(
        url,
        json=body,
        headers={"Idempotency-Key": "segment-speaker-rename-1"},
    )
    assert replayed.status_code == 200
    assert replayed.json() == payload

    reused = client.patch(
        url,
        json={**body, "display_name": "另一个姓名"},
        headers={"Idempotency-Key": "segment-speaker-rename-1"},
    )
    assert reused.status_code == 409
    assert reused.json()["detail"]["code"] == "MEETING_IDEMPOTENCY_CONFLICT"

    async def corrupt_snapshot():
        async with factory() as session:
            record = (
                await session.exec(
                    select(MeetingIdempotencyRecord).where(
                        MeetingIdempotencyRecord.owner_user_id == 7,
                        MeetingIdempotencyRecord.idempotency_key == "segment-speaker-rename-1",
                    )
                )
            ).one()
            assert record.resource_id == meeting.id
            stored = decode_json(record.response_json, {})
            stored["response"] = {"operation": "rename_segment"}
            record.response_json = json.dumps(stored)
            session.add(record)
            await session.commit()

    anyio.run(corrupt_snapshot)
    corrupted_replay = client.patch(
        url,
        json=body,
        headers={"Idempotency-Key": "segment-speaker-rename-1"},
    )
    assert corrupted_replay.status_code == 409
    assert corrupted_replay.json()["detail"]["code"] == "MEETING_IDEMPOTENCY_CONFLICT"

    blank = client.patch(
        f"/api/meetings/v1/sessions/{meeting.id}/segments/{segments[2].id}/speaker",
        json={**body, "display_name": "   ", "expected_speaker_version": 2},
    )
    assert blank.status_code == 422
    anyio.run(engine.dispose)
