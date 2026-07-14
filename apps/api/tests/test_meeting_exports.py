from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree
from zipfile import ZipFile

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from routers.meeting_exports import router
from services.auth_dependencies import get_current_user
from services.auth_service import User, UserRole
from services.meeting_contracts import (
    MEETING_TABLES,
    ArtifactState,
    ArtifactType,
    MeetingArtifact,
    MeetingEvent,
    MeetingExportCreateRequest,
    MeetingSegmentRevision,
    MeetingSession,
    MeetingSpeakerTrack,
    MeetingTranscriptSegment,
)
from services.meeting_database import get_meeting_async_session as get_async_session
from services.meeting_event_store import decode_json
from services.meeting_export import (
    MeetingExportError,
    MeetingExportService,
    MeetingExportSettings,
    MeetingExportStorage,
)
from services.meeting_repository import MeetingRepository, MeetingResourceNotFound
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession


def _user(user_id: int, role: UserRole = UserRole.ANALYST) -> User:
    return User(
        id=user_id,
        username=f"export-user-{user_id}",
        email=f"export-user-{user_id}@example.test",
        hashed_password="x",
        full_name=f"Export User {user_id}",
        role=role,
        is_active=True,
        approval_status="approved",
    )


async def _database(path: Path | None = None):
    if path is None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[User.__table__, *[model.__table__ for model in MEETING_TABLES]],
            )
        )
    return engine


def _factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _settings() -> MeetingExportSettings:
    return MeetingExportSettings(
        max_bytes=2 * 1024 * 1024,
        max_segments=10_000,
        ticket_ttl_seconds=120,
        lease_seconds=120,
        retry_delay_seconds=1,
    )


async def _seed(session: AsyncSession, *, owner_id: int = 7):
    meeting = MeetingSession(
        owner_user_id=owner_id,
        title="../../季度会\r\nX-Evil: yes",
        state="stopped",
        ai_enabled=False,
        selection_mode="none",
        last_segment_ordinal=1,
    )
    session.add(meeting)
    await session.flush()
    speaker = MeetingSpeakerTrack(
        meeting_id=meeting.id,
        track_key="speaker-1",
        anonymous_label="发言人 1",
        display_name="张三\nWEBVTT",
        label_source="manual",
    )
    session.add(speaker)
    await session.flush()
    segment = MeetingTranscriptSegment(
        meeting_id=meeting.id,
        ordinal=1,
        utterance_id="utterance-1",
        provider_segment_key="provider-1",
        start_ms=1_234,
        end_ms=4_567,
        speaker_track_id=speaker.id,
        raw_text="耐莫创\n00:00:00.000 --> 99:00:00.000 <script>alert(1)</script>",
        asr_final_text="耐莫创",
        asr_provider="meeting-speech",
        asr_model="asr-v1",
        asr_version="v1",
        human_locked=True,
    )
    session.add(segment)
    await session.flush()
    session.add(
        MeetingSegmentRevision(
            segment_id=segment.id,
            revision_no=1,
            revision_type="manual",
            text="Nemotron #最终文本 <script>alert(1)</script>\x00\x0b\x7f",
            base_revision_no=0,
            created_by=str(owner_id),
        )
    )
    await session.commit()
    return meeting, segment


async def _create_and_process(
    session: AsyncSession,
    root: Path,
    meeting: MeetingSession,
    payload: dict,
    key: str,
):
    repository = MeetingRepository(session)
    artifact, _, _, _ = await repository.create_export(
        meeting.id,
        meeting.owner_user_id,
        MeetingExportCreateRequest.model_validate(payload),
        idempotency_key=key,
    )
    service = MeetingExportService(
        session,
        settings=_settings(),
        storage=MeetingExportStorage(root, max_bytes=_settings().max_bytes),
    )
    await service.process_export(artifact.id, meeting.owner_user_id, f"test:{key}")
    artifact, job = await repository.get_export(meeting.id, artifact.id, meeting.owner_user_id)
    return service, artifact, job


def _docx_parts(payload: bytes) -> dict[str, bytes]:
    assert payload.startswith(b"PK")
    with ZipFile(BytesIO(payload)) as package:
        assert package.testzip() is None
        return {name: package.read(name) for name in package.namelist()}


def _docx_document_text(parts: dict[str, bytes]) -> str:
    root = ElementTree.fromstring(parts["word/document.xml"])
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    return "".join(node.text or "" for node in root.findall(".//w:t", namespace))


def test_transcript_export_formats_escape_content_and_preserve_evidence(tmp_path):
    async def scenario():
        engine = await _database()
        factory = _factory(engine)
        async with factory() as session:
            meeting, segment = await _seed(session)
            outputs = {}
            for export_format in ("txt", "markdown", "srt", "vtt", "json"):
                transcript_source = "asr" if export_format in {"srt", "vtt"} else "display"
                service, artifact, job = await _create_and_process(
                    session,
                    tmp_path / "exports",
                    meeting,
                    {
                        "format": export_format,
                        "content": "transcript",
                        "transcript_source": transcript_source,
                    },
                    f"format-{export_format}",
                )
                assert artifact.state == ArtifactState.READY.value
                assert job.state == "succeeded"
                metadata = decode_json(artifact.content_json, {})
                downloadable = service.storage.resolve(metadata)
                outputs[export_format] = downloadable.path.read_text(encoding="utf-8")
                assert downloadable.path.parent.name == "exports"
                assert ".." not in downloadable.filename
                assert "\r" not in downloadable.filename
                assert "\n" not in downloadable.filename
                assert metadata["sha256"] == downloadable.sha256

            assert segment.id in outputs["txt"]
            assert "00:00:01.234" in outputs["txt"]
            assert "Nemotron" in outputs["txt"]
            assert "&lt;script&gt;" in outputs["markdown"]
            assert "\\#最终文本" in outputs["markdown"]
            assert "00:00:01,234 --> 00:00:04,567" in outputs["srt"]
            assert outputs["srt"].count("-->") == 1
            assert "00:00:00.000 -> 99:00:00.000" in outputs["srt"]
            assert outputs["vtt"].startswith("WEBVTT\n")
            assert segment.id in outputs["vtt"]
            assert outputs["vtt"].count("-->") == 1
            parsed = __import__("json").loads(outputs["json"])
            assert parsed["segments"][0]["segment_id"] == segment.id
            assert parsed["segments"][0]["revision_no"] == 1
            assert parsed["segments"][0]["start_timestamp"] == "00:00:01.234"

            events = list((await session.exec(select(MeetingEvent))).all())
            assert sum(value.event_type == "export.ready" for value in events) == 5
        await engine.dispose()

    anyio.run(scenario)


def test_asr_source_and_idempotency_reuse_the_same_export(tmp_path):
    async def scenario():
        engine = await _database()
        factory = _factory(engine)
        async with factory() as session:
            meeting, _ = await _seed(session)
            request = MeetingExportCreateRequest(
                format="txt",
                content="transcript",
                transcript_source="asr",
            )
            repository = MeetingRepository(session)
            first, _, replayed, _ = await repository.create_export(
                meeting.id,
                7,
                request,
                idempotency_key="same-export-request",
            )
            assert replayed is False
            service = MeetingExportService(
                session,
                settings=_settings(),
                storage=MeetingExportStorage(tmp_path / "exports", max_bytes=_settings().max_bytes),
            )
            await service.process_export(first.id, 7, "test-idempotency")
            replay, _, replayed, _ = await repository.create_export(
                meeting.id,
                7,
                request,
                idempotency_key="same-export-request",
            )
            assert replayed is True
            assert replay.id == first.id
            artifacts = list(
                (
                    await session.exec(
                        select(MeetingArtifact).where(MeetingArtifact.artifact_type == ArtifactType.EXPORT.value)
                    )
                ).all()
            )
            assert len(artifacts) == 1
            text = service.storage.resolve(decode_json(artifacts[0].content_json, {})).path.read_text(encoding="utf-8")
            assert "耐莫创" in text
            assert "Nemotron" not in text
        await engine.dispose()

    anyio.run(scenario)


def test_minutes_export_binds_artifact_version_and_evidence_timestamp(tmp_path):
    async def scenario():
        engine = await _database()
        factory = _factory(engine)
        async with factory() as session:
            meeting, segment = await _seed(session)
            source = MeetingArtifact(
                meeting_id=meeting.id,
                artifact_type=ArtifactType.FINAL_MINUTES.value,
                version=2,
                state=ArtifactState.STALE.value,
                content_json=__import__("json").dumps(
                    {
                        "overview": "季度复盘",
                        "agenda_topics": [],
                        "chapters": [],
                        "decisions": [
                            {
                                "text": "采用 <script>Nemotron</script>",
                                "source_segment_ids": [segment.id],
                            }
                        ],
                        "open_questions": [],
                        "risks": [],
                        "action_items": [],
                        "speaker_viewpoints": [],
                    },
                    ensure_ascii=False,
                ),
                input_from_ordinal=1,
                input_to_ordinal=1,
                transcript_revision=1,
            )
            session.add(source)
            await session.commit()
            service, artifact, job = await _create_and_process(
                session,
                tmp_path / "exports",
                meeting,
                {
                    "format": "markdown",
                    "content": "minutes",
                    "transcript_source": "display",
                    "artifact_id": source.id,
                    "artifact_version": 2,
                },
                "minutes-markdown",
            )
            assert artifact.state == ArtifactState.READY.value
            input_payload = decode_json(job.input_json, {})
            assert input_payload["source_artifact_id"] == source.id
            assert input_payload["source_artifact_version"] == 2
            text = service.storage.resolve(decode_json(artifact.content_json, {})).path.read_text(encoding="utf-8")
            assert "00:00:01.234" in text
            assert segment.id in text
            assert "&lt;script&gt;Nemotron&lt;/script&gt;" in text
            assert "产物状态: `stale`" in text

            docx_service, docx_artifact, docx_job = await _create_and_process(
                session,
                tmp_path / "exports",
                meeting,
                {
                    "format": "docx",
                    "content": "minutes",
                    "transcript_source": "display",
                    "artifact_id": source.id,
                    "artifact_version": 2,
                },
                "minutes-docx",
            )
            assert docx_artifact.state == ArtifactState.READY.value
            assert docx_job.state == "succeeded"
            docx_metadata = decode_json(docx_artifact.content_json, {})
            assert docx_metadata["source_artifact_id"] == source.id
            assert docx_metadata["source_artifact_version"] == 2
            docx_path = docx_service.storage.resolve(docx_metadata).path
            parts = _docx_parts(docx_path.read_bytes())
            document_xml = parts["word/document.xml"].decode("utf-8")
            document_text = _docx_document_text(parts)
            assert "会议纪要" in document_text
            assert "纪要版本：2" in document_text
            assert "采用 <script>Nemotron</script>" in document_text
            assert "证据：00:00:01.234 - 00:00:04.567" in document_text
            assert f"来源片段：{segment.id}" in document_text
            assert "&lt;script&gt;Nemotron&lt;/script&gt;" in document_xml
        await engine.dispose()

    anyio.run(scenario)


def test_docx_transcript_is_real_office_package_with_chinese_font_and_clean_xml(tmp_path):
    async def scenario():
        engine = await _database()
        factory = _factory(engine)
        async with factory() as session:
            meeting, segment = await _seed(session)
            service, artifact, job = await _create_and_process(
                session,
                tmp_path / "exports",
                meeting,
                {
                    "format": "docx",
                    "content": "transcript",
                    "transcript_source": "display",
                },
                "transcript-docx",
            )
            assert artifact.state == ArtifactState.READY.value
            assert job.state == "succeeded"
            metadata = decode_json(artifact.content_json, {})
            downloadable = service.storage.resolve(metadata)
            assert downloadable.filename.endswith(".docx")
            assert downloadable.media_type == (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            parts = _docx_parts(downloadable.path.read_bytes())
            assert "[Content_Types].xml" in parts
            assert "word/document.xml" in parts
            assert "word/styles.xml" in parts

            document_xml = parts["word/document.xml"].decode("utf-8")
            document_text = _docx_document_text(parts)
            assert "会议逐字稿" in document_text
            assert "当前显示文字" in document_text
            assert "[00:00:01.234 - 00:00:04.567] 张三 WEBVTT" in document_text
            assert "Nemotron #最终文本 <script>alert(1)</script>" in document_text
            assert f"来源片段：{segment.id} · 修订版本：1" in document_text
            assert "耐莫创" not in document_text
            assert "&lt;script&gt;alert(1)&lt;/script&gt;" in document_xml
            assert all(character not in document_text for character in ("\x00", "\x0b", "\x7f"))

            styles_xml = parts["word/styles.xml"].decode("utf-8")
            assert 'w:eastAsia="Microsoft YaHei"' in styles_xml
            assert 'w:ascii="Microsoft YaHei"' in styles_xml
        await engine.dispose()

    anyio.run(scenario)


def test_pdf_remains_explicitly_unavailable_without_blocking_docx(tmp_path):
    async def scenario():
        engine = await _database()
        factory = _factory(engine)
        async with factory() as session:
            meeting, _ = await _seed(session)
            _, failed, failed_job = await _create_and_process(
                session,
                tmp_path / "exports",
                meeting,
                {
                    "format": "pdf",
                    "content": "transcript",
                    "transcript_source": "display",
                },
                "unsupported-pdf",
            )
            assert failed.state == ArtifactState.FAILED.value
            assert failed_job.state == "failed"
            assert failed_job.public_error_code == "EXPORT_FORMAT_NOT_AVAILABLE"
            retried_job = await MeetingRepository(session).retry_job(
                meeting.id,
                failed_job.id,
                meeting.owner_user_id,
            )
            retried_artifact, _ = await MeetingRepository(session).get_export(
                meeting.id,
                failed.id,
                meeting.owner_user_id,
            )
            assert retried_job.state == "queued"
            assert retried_artifact.state == ArtifactState.GENERATING.value

            _, ready, ready_job = await _create_and_process(
                session,
                tmp_path / "exports",
                meeting,
                {
                    "format": "docx",
                    "content": "transcript",
                    "transcript_source": "display",
                },
                "supported-after-pdf",
            )
            assert ready.state == ArtifactState.READY.value
            assert ready_job.state == "succeeded"
        await engine.dispose()

    anyio.run(scenario)


def test_docx_minutes_reject_unknown_evidence_segment(tmp_path):
    async def scenario():
        engine = await _database()
        factory = _factory(engine)
        async with factory() as session:
            meeting, _ = await _seed(session)
            source = MeetingArtifact(
                meeting_id=meeting.id,
                artifact_type=ArtifactType.FINAL_MINUTES.value,
                version=3,
                state=ArtifactState.READY.value,
                content_json=__import__("json").dumps(
                    {
                        "overview": "引用完整性测试",
                        "agenda_topics": [],
                        "chapters": [],
                        "decisions": [
                            {
                                "text": "不得导出没有来源的结论",
                                "source_segment_ids": ["missing-segment"],
                            }
                        ],
                        "open_questions": [],
                        "risks": [],
                        "action_items": [],
                        "speaker_viewpoints": [],
                    },
                    ensure_ascii=False,
                ),
                input_from_ordinal=1,
                input_to_ordinal=1,
                transcript_revision=1,
            )
            session.add(source)
            await session.commit()

            _, artifact, job = await _create_and_process(
                session,
                tmp_path / "exports",
                meeting,
                {
                    "format": "docx",
                    "content": "minutes",
                    "transcript_source": "display",
                    "artifact_id": source.id,
                    "artifact_version": source.version,
                },
                "minutes-docx-unknown-evidence",
            )
            assert artifact.state == ArtifactState.FAILED.value
            assert job.state == "failed"
            assert job.public_error_code == "EXPORT_ARTIFACT_SCHEMA_INVALID"
        await engine.dispose()

    anyio.run(scenario)


def test_download_ticket_is_owner_scoped_single_use_and_integrity_checked(tmp_path):
    async def scenario():
        engine = await _database()
        factory = _factory(engine)
        async with factory() as session:
            meeting, _ = await _seed(session)
            service, artifact, _ = await _create_and_process(
                session,
                tmp_path / "exports",
                meeting,
                {
                    "format": "txt",
                    "content": "transcript",
                    "transcript_source": "display",
                },
                "ticket-export",
            )
            raw, _, downloadable = await service.issue_ticket(
                meeting.id,
                artifact.id,
                7,
                origin="http://testserver",
            )
            with pytest.raises(MeetingResourceNotFound):
                await service.consume_ticket(meeting.id, artifact.id, 8, raw)
            consumed = await service.consume_ticket(meeting.id, artifact.id, 7, raw)
            assert consumed.path == downloadable.path
            with pytest.raises(MeetingResourceNotFound):
                await service.consume_ticket(meeting.id, artifact.id, 7, raw)

            raw, _, downloadable = await service.issue_ticket(
                meeting.id,
                artifact.id,
                7,
                origin="http://testserver",
            )
            downloadable.path.write_text("tampered", encoding="utf-8")
            with pytest.raises(MeetingExportError) as error:
                await service.consume_ticket(meeting.id, artifact.id, 7, raw)
            assert error.value.code == "EXPORT_FILE_INTEGRITY_FAILED"
        await engine.dispose()

    anyio.run(scenario)


def test_export_contract_rejects_incompatible_content_and_artifact_selection():
    transcript_docx = MeetingExportCreateRequest.model_validate(
        {"format": "docx", "content": "transcript", "transcript_source": "display"}
    )
    assert str(transcript_docx.format) == "docx"
    minutes_docx = MeetingExportCreateRequest.model_validate(
        {
            "format": "docx",
            "content": "minutes",
            "artifact_id": "artifact-1",
            "artifact_version": 1,
        }
    )
    assert str(minutes_docx.content) == "minutes"
    with pytest.raises(ValidationError):
        MeetingExportCreateRequest.model_validate({"format": "srt", "content": "minutes", "artifact_id": "artifact-1"})
    with pytest.raises(ValidationError):
        MeetingExportCreateRequest.model_validate({"format": "markdown", "content": "minutes"})
    with pytest.raises(ValidationError):
        MeetingExportCreateRequest.model_validate({"format": "txt", "content": "transcript", "artifact_version": 1})


def test_export_recovery_worker_claim_is_atomic_on_sqlite(tmp_path):
    async def scenario():
        engine = await _database(tmp_path / "worker.db")
        factory = _factory(engine)
        async with factory() as session:
            meeting, _ = await _seed(session)
            artifact, _, _, _ = await MeetingRepository(session).create_export(
                meeting.id,
                7,
                MeetingExportCreateRequest(format="txt"),
                idempotency_key="worker-recovery-export",
            )

        storage_root = tmp_path / "worker-exports"
        async with factory() as first_session, factory() as second_session:
            first = MeetingExportService(
                first_session,
                settings=_settings(),
                storage=MeetingExportStorage(storage_root, max_bytes=_settings().max_bytes),
            )
            second = MeetingExportService(
                second_session,
                settings=_settings(),
                storage=MeetingExportStorage(storage_root, max_bytes=_settings().max_bytes),
            )
            claims = await asyncio.gather(
                first.claim_next("export-worker-a"),
                second.claim_next("export-worker-b"),
            )
            claimed = [value for value in claims if value]
            assert len(claimed) == 1
            winner = first if claims[0] else second
            worker_id = "export-worker-a" if claims[0] else "export-worker-b"
            await winner.process_claimed(claimed[0], worker_id)

        async with factory() as session:
            stored, job = await MeetingRepository(session).get_export(meeting.id, artifact.id, 7)
            assert stored.state == ArtifactState.READY.value
            assert job.state == "succeeded"
            assert job.attempt == 1
        await engine.dispose()

    anyio.run(scenario)


def _router_client(tmp_path, monkeypatch):
    engine = anyio.run(_database, tmp_path / "router.db")
    factory = _factory(engine)

    async def seed():
        async with factory() as session:
            return await _seed(session)

    meeting, _ = anyio.run(seed)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    active = {"user": _user(7)}

    async def current_user():
        return active["user"]

    async def session_dependency():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_current_user] = current_user
    app.dependency_overrides[get_async_session] = session_dependency
    monkeypatch.setenv("SIQ_MEETINGS_ENABLED", "true")
    monkeypatch.setenv("SIQ_MEETINGS_ASR_ENABLED", "false")
    monkeypatch.setenv("SIQ_MEETING_EXPORT_ROOT", str(tmp_path / "router-exports"))
    return TestClient(app), active, meeting, engine


def test_export_router_queues_without_inline_render_then_worker_downloads(tmp_path, monkeypatch):
    client, active, meeting, engine = _router_client(tmp_path, monkeypatch)

    async def forbidden_inline_render(*_args, **_kwargs):
        raise AssertionError("POST must not render an export inline")

    monkeypatch.setattr(MeetingExportService, "process_export", forbidden_inline_render)
    response = client.post(
        f"/api/meetings/v1/sessions/{meeting.id}/exports",
        headers={"Idempotency-Key": "router-export-one"},
        json={
            "format": "docx",
            "content": "transcript",
            "transcript_source": "display",
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["state"] == "queued"
    assert body["download_url"] is None
    assert body["filename"] is None
    assert not (tmp_path / "router-exports").exists()
    export_id = body["id"]

    replay = client.post(
        f"/api/meetings/v1/sessions/{meeting.id}/exports",
        headers={"Idempotency-Key": "router-export-one"},
        json={
            "format": "docx",
            "content": "transcript",
            "transcript_source": "display",
        },
    )
    assert replay.status_code == 202
    assert replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json()["id"] == export_id
    assert replay.json()["state"] == "queued"
    assert replay.json()["download_url"] is None

    active["user"] = _user(8)
    hidden = client.get(f"/api/meetings/v1/sessions/{meeting.id}/exports/{export_id}")
    assert hidden.status_code == 404

    active["user"] = _user(7)
    queued = client.get(f"/api/meetings/v1/sessions/{meeting.id}/exports/{export_id}")
    assert queued.status_code == 200
    assert queued.json()["state"] == "queued"
    assert queued.json()["download_url"] is None

    async def run_worker():
        async with _factory(engine)() as session:
            service = MeetingExportService(session)
            job_id = await service.claim_next("router-export-worker")
            assert job_id == body["job_id"]
            await service.process_claimed(job_id, "router-export-worker")

    anyio.run(run_worker)

    ready = client.get(f"/api/meetings/v1/sessions/{meeting.id}/exports/{export_id}")
    assert ready.status_code == 200
    assert ready.json()["state"] == "ready"
    assert ready.json()["filename"].endswith(".docx")
    assert ready.json()["download_url"]
    download_path = urlsplit(ready.json()["download_url"])

    active["user"] = _user(8)
    wrong_download = client.get(download_path.path + "?" + download_path.query)
    assert wrong_download.status_code == 404

    active["user"] = _user(7)
    download = client.get(download_path.path + "?" + download_path.query)
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    assert "X-Evil" not in download.headers["content-disposition"]
    assert download.headers["cache-control"] == "private, no-store"
    assert download.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "来源片段" in _docx_document_text(_docx_parts(download.content))
    assert client.get(download_path.path + "?" + download_path.query).status_code == 404

    active["user"] = _user(7, UserRole.VIEWER)
    forbidden = client.post(
        f"/api/meetings/v1/sessions/{meeting.id}/exports",
        headers={"Idempotency-Key": "viewer-export"},
        json={"format": "txt", "content": "transcript"},
    )
    assert forbidden.status_code == 403

    async def audit_events():
        async with _factory(engine)() as session:
            events = list((await session.exec(select(MeetingEvent))).all())
            return [value.event_type for value in events]

    event_types = anyio.run(audit_events)
    assert "export.queued" in event_types
    assert "export.ready" in event_types
    assert "export.download_ticket.issued" in event_types
    assert "export.downloaded" in event_types
    anyio.run(engine.dispose)
