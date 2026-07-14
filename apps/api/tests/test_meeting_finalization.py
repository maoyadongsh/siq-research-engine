from __future__ import annotations

import re

import anyio
import pytest
from services.auth_service import User
from services.meeting_audio_store import MeetingAudioStore
from services.meeting_contracts import MEETING_TABLES, MeetingAudioChunk, MeetingSession
from services.meeting_finalization import (
    FinalASRSegment,
    FinalizationWindow,
    FinalWord,
    MeetingFinalizationInputInvalid,
    MeetingFinalizationService,
    MeetingFinalizationSettings,
    MeetingFinalizationUnavailable,
    align_final_segments,
)
from services.meeting_metrics import render_meeting_process_metrics
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


class FakeFinalASRClient:
    def __init__(self) -> None:
        self.calls: list[tuple[FinalizationWindow, bool, str]] = []

    async def finalize_window(self, window, *, run_id, language, hotwords, final_window):
        self.calls.append((window, final_window, run_id))
        return (
            FinalASRSegment(
                segment_token=f"final-{window.index}",
                text=f"最终文本 {window.index}",
                start_ms=window.start_ms,
                end_ms=window.end_ms,
                adapter="fake-final-asr",
                speaker_track_key="spk-f0",
                speaker_confidence=0.91,
                word_timestamps=(),
                degraded_reason=None,
                window_index=window.index,
            ),
        )


def _metric_value(rendered: str, metric: str, labels = "") -> int:
    match = re.search(rf"^{re.escape(metric)}{re.escape(labels)} (\d+)$", rendered, re.MULTILINE)
    return int(match.group(1)) if match else 0


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


def _settings() -> MeetingFinalizationSettings:
    return MeetingFinalizationSettings(
        endpoint=None,
        service_token=None,
        chunk_page_size=2,
        window_seconds=2,
        max_chunk_bytes=64_000,
        timeout_seconds=5,
        max_response_bytes=64_000,
        max_result_segments=100,
    )


async def _seed_audio(
    factory,
    store: MeetingAudioStore,
    *,
    starts: tuple[int, ...] = (0, 500, 1_000, 1_500, 2_000),
):
    async with factory() as session:
        meeting = MeetingSession(
            owner_user_id=7,
            title="finalization",
            state="stopped",
            ai_enabled=False,
            selection_mode="none",
        )
        session.add(meeting)
        await session.flush()
        for sequence, start_ms in enumerate(starts):
            payload = (sequence + 1).to_bytes(2, "little", signed=True) * 8_000
            persisted = store.persist_chunk(7, meeting.id, 1, sequence, payload)
            session.add(
                MeetingAudioChunk(
                    meeting_id=meeting.id,
                    stream_epoch=1,
                    sequence=sequence,
                    start_ms=start_ms,
                    duration_ms=500,
                    storage_key=persisted.storage_key,
                    sha256=persisted.sha256,
                    byte_size=persisted.byte_size,
                )
            )
        await session.commit()
        return meeting


def test_manifest_is_paged_and_audio_windows_stay_bounded(tmp_path):
    async def scenario():
        engine, factory = await _database()
        store = MeetingAudioStore(tmp_path / "audio")
        meeting = await _seed_audio(factory, store)
        client = FakeFinalASRClient()
        service = MeetingFinalizationService(
            factory,
            audio_store=store,
            client=client,
            settings=_settings(),
        )
        before_metrics = render_meeting_process_metrics()

        result = await service.analyze(meeting.id)
        assert result.mode == "final_asr"
        assert result.chunk_count == 5
        assert result.total_audio_bytes == 80_000
        assert result.window_count == 2
        assert len(result.segments) == 2
        assert [len(call[0].pcm) for call in client.calls] == [64_000, 16_000]
        assert all(len(call[0].pcm) <= service.settings.max_window_bytes for call in client.calls)
        assert [call[1] for call in client.calls] == [False, True]
        assert len({call[2] for call in client.calls}) == 1
        assert result.gaps == ()
        rendered = render_meeting_process_metrics()
        assert _metric_value(rendered, "meeting_final_asr_window_processing_seconds_count") == (
            _metric_value(before_metrics, "meeting_final_asr_window_processing_seconds_count") + 2
        )
        assert _metric_value(rendered, "meeting_final_asr_job_processing_seconds_count") == (
            _metric_value(before_metrics, "meeting_final_asr_job_processing_seconds_count") + 1
        )
        counter_labels = '{result="succeeded"}'
        assert _metric_value(rendered, "meeting_final_asr_window_total", counter_labels) == (
            _metric_value(before_metrics, "meeting_final_asr_window_total", counter_labels) + 2
        )
        await engine.dispose()

    anyio.run(scenario)


def test_gap_starts_a_discontinuous_window_and_tamper_fails_closed(tmp_path):
    async def scenario():
        engine, factory = await _database()
        store = MeetingAudioStore(tmp_path / "audio")
        meeting = await _seed_audio(factory, store, starts=(0, 1_000))
        client = FakeFinalASRClient()
        service = MeetingFinalizationService(
            factory,
            audio_store=store,
            client=client,
            settings=_settings(),
        )
        result = await service.analyze(meeting.id)
        assert result.gaps == ((500, 1_000),)
        assert [call[0].discontinuity for call in client.calls] == [False, True]

        async with factory() as session:
            chunk = (await session.exec(select(MeetingAudioChunk))).first()
        assert chunk is not None
        path = store.resolve_storage_key(chunk.storage_key)
        path.write_bytes(b"x" * chunk.byte_size)
        with pytest.raises(MeetingFinalizationInputInvalid):
            await service.analyze(meeting.id)
        await engine.dispose()

    anyio.run(scenario)


def test_audio_requires_configured_final_asr_but_empty_manifest_can_passthrough(tmp_path):
    async def scenario():
        engine, factory = await _database()
        store = MeetingAudioStore(tmp_path / "audio")
        meeting = await _seed_audio(factory, store, starts=(0,))
        service = MeetingFinalizationService(
            factory,
            audio_store=store,
            client=None,
            settings=_settings(),
        )
        with pytest.raises(MeetingFinalizationUnavailable):
            await service.analyze(meeting.id)

        async with factory() as session:
            empty = MeetingSession(owner_user_id=7, title="empty", state="stopped")
            session.add(empty)
            await session.commit()
        passthrough = await service.analyze(empty.id)
        assert passthrough.mode == "stable_transcript_passthrough"
        assert passthrough.segments == ()
        await engine.dispose()

    anyio.run(scenario)


def test_alignment_uses_word_timestamps_without_changing_stable_ids():
    class Stable:
        def __init__(self, identifier, ordinal, start, end):
            self.id = identifier
            self.ordinal = ordinal
            self.start_ms = start
            self.end_ms = end

    final = FinalASRSegment(
        segment_token="final-1",
        text="甲乙",
        start_ms=0,
        end_ms=1_000,
        adapter="fake",
        speaker_track_key="spk-f0",
        speaker_confidence=0.9,
        word_timestamps=(
            FinalWord(0, 100, 300, "甲"),
            FinalWord(1, 700, 900, "乙"),
        ),
        degraded_reason=None,
        window_index=0,
    )
    values = align_final_segments(
        [Stable("stable-a", 1, 0, 500), Stable("stable-b", 2, 500, 1_000)],
        [final],
    )
    assert [(value["stable_segment_id"], value["final_text"]) for value in values] == [
        ("stable-a", "甲"),
        ("stable-b", "乙"),
    ]
    assert all("embedding" not in str(value) for value in values)
