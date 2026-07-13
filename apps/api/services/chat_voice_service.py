"""Short voice-message persistence, normalization, and transcription."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import UploadFile

logger = logging.getLogger(__name__)

MAX_CHAT_VOICE_BYTES = int(os.environ.get("SIQ_CHAT_VOICE_MAX_BYTES", str(10 * 1024 * 1024)))
MAX_CHAT_VOICE_SECONDS = float(os.environ.get("SIQ_CHAT_VOICE_MAX_SECONDS", "60"))
FUNASR_BASE_URL = os.environ.get("SIQ_FUNASR_BASE_URL", "http://127.0.0.1:8899/asr").strip()
FUNASR_TIMEOUT_SECONDS = float(os.environ.get("SIQ_FUNASR_TIMEOUT_SECONDS", "90"))
FFMPEG_BIN = os.environ.get("SIQ_FFMPEG_BIN", "ffmpeg").strip() or "ffmpeg"
FFMPEG_TIMEOUT_SECONDS = float(os.environ.get("SIQ_CHAT_VOICE_FFMPEG_TIMEOUT_SECONDS", "30"))
UPLOAD_CHUNK_BYTES = 1024 * 1024

AUDIO_CONTENT_TYPES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/aac": ".aac",
}
AUDIO_SUFFIX_TYPES = {
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
}
GENERIC_UPLOAD_CONTENT_TYPES = {"", "application/octet-stream"}
LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class ChatVoiceError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class ChatVoiceTranscription:
    attachment_id: str
    filename: str
    stored_name: str
    content_type: str
    size: int
    path: Path
    text: str
    duration: float
    language: str
    provider: str = "funasr"


def _normalized_content_type(value: str | None) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _display_filename(value: str | None, extension: str) -> str:
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\r\n\t]+", " ", name).strip()[:160]
    return name or f"voice{extension}"


def _safe_stored_filename(value: str | None, extension: str) -> str:
    stem = Path(str(value or "")).stem.strip()
    stem = re.sub(r"[^0-9A-Za-z._-]+", "_", stem).strip("._-")
    return f"{(stem[:64] or 'voice')}{extension}"


def resolve_audio_upload_type(filename: str | None, content_type: str | None) -> tuple[str, str]:
    declared_type = _normalized_content_type(content_type)
    suffix = Path(str(filename or "")).suffix.lower()
    if declared_type in AUDIO_CONTENT_TYPES:
        return declared_type, AUDIO_CONTENT_TYPES[declared_type]
    if declared_type in GENERIC_UPLOAD_CONTENT_TYPES and suffix in AUDIO_SUFFIX_TYPES:
        effective_type = AUDIO_SUFFIX_TYPES[suffix]
        return effective_type, AUDIO_CONTENT_TYPES[effective_type]
    raise ChatVoiceError(415, "Only browser-recorded WebM, OGG, M4A, MP3, WAV, or AAC audio is supported")


def normalize_language(value: str | None) -> str:
    language = str(value or "zh").strip() or "zh"
    if not LANGUAGE_RE.fullmatch(language):
        raise ChatVoiceError(400, "Invalid transcription language")
    return language


async def _save_upload_limited(upload: UploadFile, target: Path) -> int:
    total = 0
    try:
        with target.open("xb") as output:
            while True:
                chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_CHAT_VOICE_BYTES:
                    limit_mb = MAX_CHAT_VOICE_BYTES / (1024 * 1024)
                    raise ChatVoiceError(413, f"Voice message exceeds {limit_mb:g} MB")
                output.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if total <= 0:
        target.unlink(missing_ok=True)
        raise ChatVoiceError(400, "Voice message is empty")
    return total


async def _normalize_to_wav(source: Path, target: Path) -> None:
    # Decode only slightly past the accepted limit so malformed or very long input
    # cannot make ffmpeg create an unbounded temporary PCM file.
    decode_limit = max(MAX_CHAT_VOICE_SECONDS + 0.1, 0.1)
    args = [
        FFMPEG_BIN,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-t",
        f"{decode_limit:.3f}",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(target),
    ]
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ChatVoiceError(503, "Audio converter is unavailable") from exc
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=FFMPEG_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise ChatVoiceError(504, "Audio conversion timed out") from exc
    if process.returncode != 0 or not target.is_file():
        diagnostic = (stderr or b"").decode("utf-8", errors="replace").strip()
        if diagnostic:
            logger.info("chat voice ffmpeg rejected upload: %s", diagnostic[:500])
        raise ChatVoiceError(400, "Voice message is not valid decodable audio")


def _wav_duration_seconds(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            frame_rate = audio.getframerate()
            frame_count = audio.getnframes()
    except (OSError, EOFError, wave.Error) as exc:
        raise ChatVoiceError(400, "Normalized voice message is invalid") from exc
    if frame_rate <= 0 or frame_count <= 0:
        raise ChatVoiceError(400, "Voice message contains no audio")
    return frame_count / frame_rate


def _funasr_endpoint() -> str:
    endpoint = FUNASR_BASE_URL.rstrip("/")
    parsed = urlparse(endpoint)
    if parsed.path in {"", "/"}:
        endpoint += "/asr"
    return endpoint


def _extract_transcript(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    text = payload.get("text")
    if isinstance(text, str):
        return text.strip()
    result = payload.get("result")
    if isinstance(result, dict):
        return str(result.get("text") or "").strip()
    if isinstance(result, list):
        pieces = [str(item.get("text") or "").strip() for item in result if isinstance(item, dict)]
        return "".join(piece for piece in pieces if piece).strip()
    return ""


async def _transcribe_with_funasr(path: Path, *, language: str) -> str:
    timeout = httpx.Timeout(FUNASR_TIMEOUT_SECONDS, connect=5.0, read=FUNASR_TIMEOUT_SECONDS)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            with path.open("rb") as audio:
                response = await client.post(
                    _funasr_endpoint(),
                    data={"language": language, "spk": "false", "timestamp": "false"},
                    files={"file": ("voice.wav", audio, "audio/wav")},
                )
    except httpx.TimeoutException as exc:
        raise ChatVoiceError(504, "Speech transcription timed out") from exc
    except (httpx.RequestError, OSError) as exc:
        raise ChatVoiceError(502, "Speech transcription service is unavailable") from exc
    if not response.is_success:
        logger.warning("FunASR returned HTTP %s: %s", response.status_code, response.text[:500])
        raise ChatVoiceError(502, "Speech transcription service rejected the audio")
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ChatVoiceError(502, "Speech transcription service returned an invalid response") from exc
    text = _extract_transcript(payload)
    if not text:
        raise ChatVoiceError(422, "No speech was recognized")
    return text


async def transcribe_chat_voice(
    upload: UploadFile,
    *,
    user_upload_dir: Path,
    language: str | None = "zh",
) -> ChatVoiceTranscription:
    effective_type, extension = resolve_audio_upload_type(upload.filename, upload.content_type)
    normalized_language = normalize_language(language)
    attachment_id = uuid.uuid4().hex
    normalized_name = _safe_stored_filename(upload.filename, extension)
    stored_name = f"{attachment_id}_{normalized_name}"
    original_path = user_upload_dir / stored_name
    wav_path = user_upload_dir / f".{attachment_id}.normalized.wav"
    user_upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        size = await _save_upload_limited(upload, original_path)
        await _normalize_to_wav(original_path, wav_path)
        duration = _wav_duration_seconds(wav_path)
        if duration > MAX_CHAT_VOICE_SECONDS:
            raise ChatVoiceError(413, f"Voice message exceeds {MAX_CHAT_VOICE_SECONDS:g} seconds")
        text = await _transcribe_with_funasr(wav_path, language=normalized_language)
    except Exception:
        original_path.unlink(missing_ok=True)
        raise
    finally:
        wav_path.unlink(missing_ok=True)
        await upload.close()
    return ChatVoiceTranscription(
        attachment_id=attachment_id,
        filename=_display_filename(upload.filename, extension),
        stored_name=stored_name,
        content_type=effective_type,
        size=size,
        path=original_path,
        text=text,
        duration=round(duration, 3),
        language=normalized_language,
    )


__all__ = [
    "AUDIO_CONTENT_TYPES",
    "AUDIO_SUFFIX_TYPES",
    "ChatVoiceError",
    "ChatVoiceTranscription",
    "transcribe_chat_voice",
]
