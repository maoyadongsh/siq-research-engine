from __future__ import annotations

import asyncio
import re

import anyio
import httpx
import pytest
from services.auth_service import User
from services.meeting_audio_store import MeetingAudioStore
from services.meeting_contracts import MEETING_TABLES, MeetingAudioChunk, MeetingSession
from services.meeting_finalization import (
    FINAL_ASR_INDEPENDENT_PROTOCOL,
    FinalASRSegment,
    FinalASRWindowResult,
    FinalizationWindow,
    FinalWord,
    HttpFinalASRClient,
    MeetingFinalizationInputInvalid,
    MeetingFinalizationOutputInvalid,
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

TEST_DIARIZER_REF = "diarizer-test-v1"


class FakeFinalASRClient:
    def __init__(self) -> None:
        self.calls: list[tuple[FinalizationWindow, bool, str]] = []

    async def finalize_window(self, window, *, run_id, language, hotwords, final_window):
        self.calls.append((window, final_window, run_id))
        return FinalASRWindowResult(
            diarizer_ref=TEST_DIARIZER_REF,
            segments=(
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
                    diarizer_ref=TEST_DIARIZER_REF,
                ),
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


def _settings(*, max_concurrency: int = 2, window_overlap_ms: int = 0) -> MeetingFinalizationSettings:
    return MeetingFinalizationSettings(
        endpoint=None,
        service_token=None,
        chunk_page_size=2,
        window_seconds=2,
        max_chunk_bytes=64_000,
        timeout_seconds=5,
        max_response_bytes=64_000,
        max_result_segments=100,
        max_concurrency=max_concurrency,
        window_overlap_ms=window_overlap_ms,
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
        assert result.diarizer_ref == TEST_DIARIZER_REF
        assert len(result.segments) == 2
        assert [len(call[0].pcm) for call in client.calls] == [64_000, 16_000]
        assert all(len(call[0].pcm) <= service.settings.max_window_bytes for call in client.calls)
        assert [call[1] for call in client.calls] == [True, True]
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


def test_final_asr_processes_independent_windows_with_bounded_concurrency(tmp_path):
    class ConcurrencyProbeClient(FakeFinalASRClient):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.peak = 0

        async def finalize_window(self, window, *, run_id, language, hotwords, final_window):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.03 if window.index % 2 == 0 else 0.005)
                return await super().finalize_window(
                    window,
                    run_id=run_id,
                    language=language,
                    hotwords=hotwords,
                    final_window=final_window,
                )
            finally:
                self.active -= 1

    async def scenario():
        engine, factory = await _database()
        store = MeetingAudioStore(tmp_path / "audio")
        meeting = await _seed_audio(factory, store, starts=tuple(range(0, 8_000, 500)))
        client = ConcurrencyProbeClient()
        service = MeetingFinalizationService(
            factory,
            audio_store=store,
            client=client,
            settings=_settings(),
        )

        result = await service.analyze(meeting.id)

        assert result.window_count == 4
        assert client.peak == 2
        assert [segment.window_index for segment in result.segments] == [0, 1, 2, 3]
        assert result.max_concurrency == 2
        assert result.protocol_version == FINAL_ASR_INDEPENDENT_PROTOCOL
        await engine.dispose()

    anyio.run(scenario)


def test_overlap_uses_word_timestamps_for_deterministic_boundary_deduplication(tmp_path):
    class OverlapClient(FakeFinalASRClient):
        async def finalize_window(self, window, *, run_id, language, hotwords, final_window):
            self.calls.append((window, final_window, run_id))
            words = tuple(
                FinalWord(
                    token_index=index,
                    start_ms=start_ms,
                    end_ms=start_ms + 500,
                    text=f"词{start_ms}",
                )
                for index, start_ms in enumerate(range(window.start_ms, window.end_ms, 500))
            )
            return FinalASRWindowResult(
                diarizer_ref=TEST_DIARIZER_REF,
                segments=(
                    FinalASRSegment(
                        segment_token=f"overlap-{window.index}",
                        text="".join(word.text or "" for word in words),
                        start_ms=words[0].start_ms,
                        end_ms=words[-1].end_ms,
                        adapter="fake-final-asr",
                        speaker_track_key="spk-f0",
                        speaker_confidence=0.91,
                        word_timestamps=words,
                        degraded_reason=None,
                        window_index=window.index,
                        diarizer_ref=TEST_DIARIZER_REF,
                    ),
                ),
            )

    async def scenario():
        engine, factory = await _database()
        store = MeetingAudioStore(tmp_path / "audio")
        meeting = await _seed_audio(factory, store, starts=tuple(range(0, 4_500, 500)))
        client = OverlapClient()
        service = MeetingFinalizationService(
            factory,
            audio_store=store,
            client=client,
            settings=_settings(window_overlap_ms=500),
        )

        result = await service.analyze(meeting.id)

        assert sorted((call[0].start_ms, call[0].end_ms) for call in client.calls) == [
            (0, 2_000),
            (1_500, 3_500),
            (3_000, 4_500),
        ]
        kept_words = [word for segment in result.segments for word in segment.word_timestamps]
        assert [word.start_ms for word in kept_words] == list(range(0, 4_500, 500))
        assert len({word.start_ms for word in kept_words}) == len(kept_words)
        assert result.boundary_trimmed_segment_count == 2
        assert result.window_overlap_ms == 500
        assert result.segments[0].segment_token.endswith(":w0-boundary-0-2")
        assert result.segments[1].segment_token.endswith(":w1-boundary-0-2")
        await engine.dispose()

    anyio.run(scenario)


def test_overlap_assigns_segment_without_words_to_one_window(tmp_path):
    class SegmentOverlapClient(FakeFinalASRClient):
        async def finalize_window(self, window, *, run_id, language, hotwords, final_window):
            self.calls.append((window, final_window, run_id))
            return FinalASRWindowResult(
                diarizer_ref=TEST_DIARIZER_REF,
                segments=(
                    FinalASRSegment(
                        segment_token=f"duplicate-{window.index}",
                        text="同一边界片段",
                        start_ms=1_600,
                        end_ms=1_900,
                        adapter="fake-final-asr",
                        speaker_track_key="spk-f0",
                        speaker_confidence=0.91,
                        word_timestamps=(),
                        degraded_reason=None,
                        window_index=window.index,
                        diarizer_ref=TEST_DIARIZER_REF,
                    ),
                ),
            )

    async def scenario():
        engine, factory = await _database()
        store = MeetingAudioStore(tmp_path / "audio")
        meeting = await _seed_audio(factory, store, starts=tuple(range(0, 3_500, 500)))
        service = MeetingFinalizationService(
            factory,
            audio_store=store,
            client=SegmentOverlapClient(),
            settings=_settings(window_overlap_ms=500),
        )

        result = await service.analyze(meeting.id)

        assert [(segment.segment_token, segment.window_index) for segment in result.segments] == [
            ("duplicate-1", 1)
        ]
        assert result.boundary_trimmed_segment_count == 1
        await engine.dispose()

    anyio.run(scenario)


def test_retry_reuses_stable_finalization_run_id(tmp_path):
    class RetryClient(FakeFinalASRClient):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False

        async def finalize_window(self, window, *, run_id, language, hotwords, final_window):
            self.calls.append((window, final_window, run_id))
            if window.index == 1 and not self.failed:
                self.failed = True
                raise MeetingFinalizationUnavailable("transient")
            return await super().finalize_window(
                window,
                run_id=run_id,
                language=language,
                hotwords=hotwords,
                final_window=final_window,
            )

    async def scenario():
        engine, factory = await _database()
        store = MeetingAudioStore(tmp_path / "audio")
        meeting = await _seed_audio(factory, store)
        client = RetryClient()
        service = MeetingFinalizationService(
            factory,
            audio_store=store,
            client=client,
            settings=_settings(),
        )

        with pytest.raises(MeetingFinalizationUnavailable, match="transient"):
            await service.analyze(meeting.id, run_id="durable-job-id")
        result = await service.analyze(meeting.id, run_id="durable-job-id")

        assert result.window_count == 2
        assert len({run_id for _, _, run_id in client.calls}) == 1
        assert all(re.fullmatch(r"[0-9a-f-]{36}", run_id) for _, _, run_id in client.calls)
        await engine.dispose()

    anyio.run(scenario)


def test_final_asr_rejects_diarizer_change_across_empty_windows(tmp_path):
    class ChangingDiarizerClient(FakeFinalASRClient):
        async def finalize_window(self, window, *, run_id, language, hotwords, final_window):
            self.calls.append((window, final_window, run_id))
            return FinalASRWindowResult(
                diarizer_ref=(TEST_DIARIZER_REF if window.index == 0 else "diarizer-other-v1"),
                segments=(),
            )

    async def scenario():
        engine, factory = await _database()
        store = MeetingAudioStore(tmp_path / "audio")
        meeting = await _seed_audio(factory, store)
        service = MeetingFinalizationService(
            factory,
            audio_store=store,
            client=ChangingDiarizerClient(),
            settings=_settings(),
        )

        with pytest.raises(MeetingFinalizationOutputInvalid, match="changed between windows"):
            await service.analyze(meeting.id)
        await engine.dispose()

    anyio.run(scenario)


def test_http_final_asr_contract_requires_a_valid_diarizer_ref():
    async def scenario():
        response_payload = {
            "schema_version": "siq.meeting.final_asr_window.v1",
            "protocol_version": FINAL_ASR_INDEPENDENT_PROTOCOL,
            "diarizer_ref": TEST_DIARIZER_REF,
            "segments": [],
        }

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=response_payload)

        raw_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        settings = MeetingFinalizationSettings(
            endpoint="http://localhost/v1/finalize-window",
            service_token="internal-test-token",
            chunk_page_size=2,
            window_seconds=2,
            max_chunk_bytes=64_000,
            timeout_seconds=5,
            max_response_bytes=64_000,
            max_result_segments=100,
            max_concurrency=2,
            window_overlap_ms=0,
        )
        client = HttpFinalASRClient(settings, client=raw_client)
        window = FinalizationWindow(index=0, start_ms=0, end_ms=1_000, pcm=b"\0\0", discontinuity=False)

        valid = await client.finalize_window(
            window,
            run_id="finalization-test",
            language="zh-CN",
            hotwords=(),
            final_window=True,
        )
        assert valid.diarizer_ref == TEST_DIARIZER_REF

        response_payload.pop("diarizer_ref")
        with pytest.raises(MeetingFinalizationOutputInvalid, match="diarizer identity"):
            await client.finalize_window(
                window,
                run_id="finalization-test",
                language="zh-CN",
                hotwords=(),
                final_window=True,
            )
        await raw_client.aclose()

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
        assert passthrough.diarizer_ref is None
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
        diarizer_ref=TEST_DIARIZER_REF,
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
