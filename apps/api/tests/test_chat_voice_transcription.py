import io
import json
import wave
from pathlib import Path
from types import SimpleNamespace

import anyio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from models import ChatMessage
from routers import chat
from services.auth_dependencies import get_current_user
from services.chat_voice_service import ChatVoiceError, ChatVoiceTranscription
from starlette.datastructures import Headers, UploadFile

from services import agent_runtime_attachments, agent_runtime_context, agent_runtime_display, chat_voice_service


def _wav_bytes(duration: float = 0.1, frame_rate: int = 16_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(frame_rate)
        audio.writeframes(b"\x00\x00" * max(1, round(duration * frame_rate)))
    return output.getvalue()


def _upload(raw: bytes, *, filename: str = "voice.webm", content_type: str = "audio/webm;codecs=opus"):
    return UploadFile(
        io.BytesIO(raw),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


def test_transcribe_chat_voice_persists_original_and_removes_normalized_wav(monkeypatch, tmp_path):
    raw = _wav_bytes(duration=0.25)
    upload = _upload(raw, filename="recording.wav", content_type="audio/wav")
    seen = {}

    async def fake_normalize(source: Path, target: Path):
        seen["source"] = source
        target.write_bytes(source.read_bytes())

    async def fake_transcribe(path: Path, *, language: str):
        seen["wav"] = path
        seen["language"] = language
        assert path.is_file()
        return "请比较这两家公司的利润率"

    monkeypatch.setattr(chat_voice_service, "_normalize_to_wav", fake_normalize)
    monkeypatch.setattr(chat_voice_service, "_transcribe_with_funasr", fake_transcribe)

    async def run_case():
        return await chat_voice_service.transcribe_chat_voice(
            upload,
            user_upload_dir=tmp_path / "7",
            language="zh",
        )

    result = anyio.run(run_case)

    assert result.text == "请比较这两家公司的利润率"
    assert result.duration == 0.25
    assert result.language == "zh"
    assert result.provider == "funasr"
    assert result.path.read_bytes() == raw
    assert result.path.parent == tmp_path / "7"
    assert not seen["wav"].exists()
    assert seen["language"] == "zh"


def test_transcribe_chat_voice_rejects_audio_over_60_seconds_and_removes_original(monkeypatch, tmp_path):
    upload = _upload(_wav_bytes(), filename="recording.wav", content_type="audio/wav")

    async def fake_normalize(source: Path, target: Path):
        target.write_bytes(source.read_bytes())

    monkeypatch.setattr(chat_voice_service, "_normalize_to_wav", fake_normalize)
    monkeypatch.setattr(chat_voice_service, "_wav_duration_seconds", lambda _path: 60.001)

    async def run_case():
        return await chat_voice_service.transcribe_chat_voice(
            upload,
            user_upload_dir=tmp_path / "8",
            language="zh",
        )

    try:
        anyio.run(run_case)
    except ChatVoiceError as exc:
        assert exc.status_code == 413
        assert exc.detail == "Voice message exceeds 60 seconds"
    else:
        raise AssertionError("expected voice duration limit error")

    assert list((tmp_path / "8").iterdir()) == []


def test_transcribe_chat_voice_rejects_oversized_upload_before_asr(monkeypatch, tmp_path):
    upload = _upload(b"12345")
    monkeypatch.setattr(chat_voice_service, "MAX_CHAT_VOICE_BYTES", 4)

    async def run_case():
        return await chat_voice_service.transcribe_chat_voice(
            upload,
            user_upload_dir=tmp_path / "9",
            language="zh",
        )

    try:
        anyio.run(run_case)
    except ChatVoiceError as exc:
        assert exc.status_code == 413
    else:
        raise AssertionError("expected voice upload size limit error")

    assert list((tmp_path / "9").iterdir()) == []


def test_funasr_request_disables_speaker_and_timestamps(monkeypatch, tmp_path):
    wav_path = tmp_path / "voice.wav"
    wav_path.write_bytes(_wav_bytes())
    captured = {}

    class FakeResponse:
        is_success = True
        content = b"response"

        def json(self):
            return {"text": "转写完成"}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, data, files):
            captured.update({"url": url, "data": data, "files": files})
            assert files["file"][2] == "audio/wav"
            assert files["file"][1].read(4) == b"RIFF"
            return FakeResponse()

    monkeypatch.setattr(chat_voice_service.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(chat_voice_service, "FUNASR_BASE_URL", "http://127.0.0.1:8899")

    async def run_case():
        return await chat_voice_service._transcribe_with_funasr(wav_path, language="zh")

    text = anyio.run(run_case)

    assert text == "转写完成"
    assert captured["url"] == "http://127.0.0.1:8899/asr"
    assert captured["data"] == {"language": "zh", "spk": "false", "timestamp": "false"}
    assert captured["client_kwargs"]["trust_env"] is False


def test_chat_transcribe_returns_replayable_audio_attachment(monkeypatch, tmp_path):
    stored_path = tmp_path / "chat_uploads" / "42" / "voice-id_voice.webm"
    stored_path.parent.mkdir(parents=True)
    stored_path.write_bytes(b"voice-data")

    async def fake_transcribe(_file, *, user_upload_dir: Path, language: str):
        assert user_upload_dir.name == "42"
        assert language == "zh"
        return ChatVoiceTranscription(
            attachment_id="voice-id",
            filename="voice.webm",
            stored_name=stored_path.name,
            content_type="audio/webm",
            size=10,
            path=stored_path,
            text="分析一下这家公司",
            duration=3.25,
            language="zh",
        )

    monkeypatch.setattr(chat, "transcribe_chat_voice", fake_transcribe)
    monkeypatch.setattr(chat, "CHAT_UPLOAD_DIR", tmp_path / "chat_uploads")
    app = FastAPI()
    app.include_router(chat.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=42)

    with TestClient(app) as client:
        response = client.post(
            "/api/chat/transcribe",
            files={"file": ("voice.webm", b"voice-data", "audio/webm")},
            data={"language": "zh"},
        )
        payload = response.json()
        replay = client.get(payload["attachment"]["url"])
        replay_range = client.get(payload["attachment"]["url"], headers={"Range": "bytes=0-4"})

    assert response.status_code == 200
    assert payload["text"] == "分析一下这家公司"
    assert payload["duration"] == 3.25
    assert payload["provider"] == "funasr"
    assert payload["attachment"]["kind"] == "audio"
    assert payload["attachment"]["metadata"] == {
        "duration": 3.25,
        "duration_ms": 3250,
        "transcript": "分析一下这家公司",
        "transcription_status": "completed",
        "language": "zh",
        "provider": "funasr",
    }
    assert replay.status_code == 200
    assert replay.content == b"voice-data"
    assert replay.headers["content-type"] == "audio/webm"
    assert replay.headers["accept-ranges"] == "bytes"
    assert replay_range.status_code == 206
    assert replay_range.content == b"voice"
    assert replay_range.headers["content-range"] == "bytes 0-4/10"


def test_chat_request_rejects_cross_user_audio_attachment(tmp_path, monkeypatch):
    owner_path = tmp_path / "chat_uploads" / "7" / "voice-id_voice.webm"
    owner_path.parent.mkdir(parents=True)
    owner_path.write_bytes(b"voice-data")
    monkeypatch.setattr(chat, "CHAT_UPLOAD_DIR", tmp_path / "chat_uploads")
    attachment = chat.ChatAttachment(
        id="voice-id",
        filename="voice.webm",
        content_type="audio/webm",
        size=10,
        path=str(owner_path),
        url=f"/api/chat/attachments/{owner_path.name}",
        kind="audio",
        metadata={"transcript": "分析一下这家公司"},
    )

    try:
        chat._validate_chat_audio_attachments([attachment], user_id=8)
    except chat.HTTPException as exc:
        assert exc.status_code == 403
    else:
        raise AssertionError("expected cross-user voice attachment rejection")

    assert chat._validate_chat_audio_attachments([attachment], user_id=7) == [attachment]


def test_audio_attachment_is_not_selected_for_image_or_document_analysis():
    audio = {
        "id": "voice-id",
        "filename": "voice.webm",
        "content_type": "audio/webm",
        "size": 100,
        "path": "/tmp/voice.webm",
        "url": "/api/chat/attachments/voice.webm",
        "kind": "audio",
        "metadata": {"transcript": "继续分析"},
    }

    assert agent_runtime_context.image_attachment_dicts([audio]) == []
    assert agent_runtime_context.document_attachment_dicts([audio]) == []
    document_context = agent_runtime_attachments._document_attachment_context([audio])
    assert document_context == ""
    assert agent_runtime_context.build_hermes_run_input_payload(
        "继续分析",
        has_attachments=True,
        document_context=document_context,
    ) == "继续分析"
    assert agent_runtime_display._display_message_with_attachments(
        "继续分析",
        [audio],
    ) == "继续分析"
    assert agent_runtime_attachments._attachment_reference_context([audio]) == ""
    stored = ChatMessage(
        role="user",
        content="继续分析",
        session_id="user-1-assistant-voice",
        attachments_json=json.dumps([audio], ensure_ascii=False),
    )
    assert agent_runtime_attachments._message_attachments(stored) == [audio]


def test_chat_transcribe_route_requires_current_user_dependency():
    route = next(route for route in chat.router.routes if route.path == "/chat/transcribe")

    assert any(dependency.call is get_current_user for dependency in route.dependant.dependencies)
