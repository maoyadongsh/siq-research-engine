"""Controlled, atomic meeting audio storage and bounded WAV packing."""

from __future__ import annotations

import hashlib
import os
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from services.meeting_contracts import MeetingAudioChunk
from services.path_config import BACKEND_DATA_ROOT

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class MeetingAudioStoreError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PersistedAudioChunk:
    storage_key: str
    sha256: str
    byte_size: int
    created: bool


def _validate_component(value: str) -> str:
    if not _ID.fullmatch(value):
        raise MeetingAudioStoreError("AUDIO_STORAGE_ID_INVALID", "audio storage identifier is invalid")
    return value


class MeetingAudioStore:
    def __init__(self, root: Path | None = None) -> None:
        configured = (os.getenv("SIQ_MEETING_AUDIO_ROOT") or os.getenv("SIQ_MEETINGS_AUDIO_ROOT") or "").strip()
        requested_root = (root or (Path(configured) if configured else BACKEND_DATA_ROOT / "meeting_audio")).expanduser()
        if requested_root.is_symlink():
            raise MeetingAudioStoreError("AUDIO_STORAGE_PATH_INVALID", "audio storage root cannot be a symlink")
        self.root = requested_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _meeting_root(self, owner_user_id: int, meeting_id: str) -> Path:
        owner = _validate_component(str(owner_user_id))
        meeting = _validate_component(meeting_id)
        owner_root = self.root / owner
        requested = owner_root / meeting
        if owner_root.is_symlink() or requested.is_symlink():
            raise MeetingAudioStoreError("AUDIO_STORAGE_PATH_INVALID", "symlinked audio paths are forbidden")
        path = requested.resolve()
        if self.root not in path.parents:
            raise MeetingAudioStoreError("AUDIO_STORAGE_PATH_INVALID", "audio path escaped its root")
        return path

    def resolve_storage_key(self, storage_key: str) -> Path:
        if not storage_key or Path(storage_key).is_absolute():
            raise MeetingAudioStoreError("AUDIO_STORAGE_KEY_INVALID", "audio storage key is invalid")
        path = (self.root / storage_key).resolve()
        if self.root not in path.parents:
            raise MeetingAudioStoreError("AUDIO_STORAGE_KEY_INVALID", "audio storage key escaped its root")
        return path

    def persist_chunk(
        self,
        owner_user_id: int,
        meeting_id: str,
        stream_epoch: int,
        sequence: int,
        payload: bytes,
    ) -> PersistedAudioChunk:
        if stream_epoch < 1 or sequence < 0 or not payload:
            raise MeetingAudioStoreError("AUDIO_CHUNK_INVALID", "audio chunk coordinates are invalid")
        digest = hashlib.sha256(payload).hexdigest()
        meeting_root = self._meeting_root(owner_user_id, meeting_id)
        target = meeting_root / "chunks" / str(stream_epoch) / f"{sequence}.pcm"
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        storage_key = target.relative_to(self.root).as_posix()
        if target.exists():
            existing_digest = self._file_sha256(target)
            if existing_digest != digest or target.stat().st_size != len(payload):
                raise MeetingAudioStoreError(
                    "AUDIO_SEQUENCE_CONFLICT",
                    "audio sequence already exists with different content",
                )
            return PersistedAudioChunk(storage_key, digest, len(payload), False)

        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            packed = meeting_root / "audio" / "meeting.wav"
            packed.unlink(missing_ok=True)
        except FileExistsError:
            return self.persist_chunk(owner_user_id, meeting_id, stream_epoch, sequence, payload)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise MeetingAudioStoreError("AUDIO_STORAGE_WRITE_FAILED", "audio storage is unavailable") from exc
        return PersistedAudioChunk(storage_key, digest, len(payload), True)

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _verified_chunk_path(
        self,
        owner_user_id: int,
        meeting_id: str,
        chunk: MeetingAudioChunk,
    ) -> Path:
        meeting_root = self._meeting_root(owner_user_id, meeting_id)
        path = self.resolve_storage_key(chunk.storage_key)
        if meeting_root not in path.parents:
            raise MeetingAudioStoreError(
                "AUDIO_STORAGE_KEY_INVALID",
                "audio chunk does not belong to this meeting",
            )
        try:
            size = path.stat().st_size
            digest = self._file_sha256(path)
        except OSError as exc:
            raise MeetingAudioStoreError(
                "AUDIO_INTEGRITY_FAILED",
                "audio chunk integrity could not be verified",
            ) from exc
        if size != chunk.byte_size or digest != chunk.sha256:
            raise MeetingAudioStoreError(
                "AUDIO_INTEGRITY_FAILED",
                "audio chunk does not match its durable manifest",
            )
        return path

    def remove_chunk_if_created(self, persisted: PersistedAudioChunk) -> None:
        if persisted.created:
            self.resolve_storage_key(persisted.storage_key).unlink(missing_ok=True)

    def read_verified_chunk(
        self,
        owner_user_id: int,
        meeting_id: str,
        chunk: MeetingAudioChunk,
        *,
        max_bytes: int,
    ) -> bytes:
        """Read one manifest-verified PCM chunk without crossing a caller bound."""

        if max_bytes <= 0 or chunk.byte_size > max_bytes:
            raise MeetingAudioStoreError(
                "AUDIO_CHUNK_TOO_LARGE",
                "audio chunk exceeds the finalization read bound",
            )
        if (
            chunk.codec != "pcm_s16le"
            or chunk.sample_rate != 16_000
            or chunk.channels != 1
            or chunk.byte_size != chunk.duration_ms * 32
        ):
            raise MeetingAudioStoreError(
                "AUDIO_FORMAT_UNSUPPORTED",
                "audio chunk manifest is not 16 kHz mono PCM16",
            )
        path = self._verified_chunk_path(owner_user_id, meeting_id, chunk)
        try:
            with path.open("rb") as source:
                payload = source.read(max_bytes + 1)
        except OSError as exc:
            raise MeetingAudioStoreError(
                "AUDIO_STORAGE_READ_FAILED",
                "audio chunk is unavailable",
            ) from exc
        if len(payload) != chunk.byte_size:
            raise MeetingAudioStoreError(
                "AUDIO_CHUNK_TRUNCATED",
                "audio chunk length changed after manifest verification",
            )
        return payload

    def packed_audio_path(self, owner_user_id: int, meeting_id: str) -> Path:
        audio_root = self._meeting_root(owner_user_id, meeting_id) / "audio"
        target = audio_root / "meeting.wav"
        if audio_root.is_symlink() or target.is_symlink():
            raise MeetingAudioStoreError("AUDIO_STORAGE_PATH_INVALID", "symlinked packed audio is forbidden")
        return target

    def ready_packed_audio_path(self, owner_user_id: int, meeting_id: str) -> Path | None:
        """Return an atomically published WAV without re-reading every PCM chunk."""

        try:
            target = self.packed_audio_path(owner_user_id, meeting_id)
            if not target.is_file() or target.stat().st_size < 44:
                return None
            with target.open("rb") as source:
                header = source.read(12)
        except (MeetingAudioStoreError, OSError):
            return None
        return target if header[:4] == b"RIFF" and header[8:12] == b"WAVE" else None

    def read_pcm_range(
        self,
        owner_user_id: int,
        meeting_id: str,
        chunks: list[MeetingAudioChunk],
        start_ms: int,
        end_ms: int,
        max_bytes: int,
    ) -> bytes:
        if start_ms < 0 or end_ms <= start_ms or max_bytes <= 0:
            raise MeetingAudioStoreError("AUDIO_RANGE_INVALID", "PCM range is invalid")
        expected_bytes = (end_ms - start_ms) * 32
        if expected_bytes > max_bytes:
            raise MeetingAudioStoreError("AUDIO_RANGE_TOO_LARGE", "PCM range exceeds its byte limit")
        selected = sorted(chunks, key=lambda item: (item.start_ms, item.stream_epoch, item.sequence))
        cursor_ms = start_ms
        output = bytearray()
        for chunk in selected:
            chunk_end = chunk.start_ms + chunk.duration_ms
            if chunk_end <= cursor_ms or chunk.start_ms >= end_ms:
                continue
            if chunk.codec != "pcm_s16le" or chunk.sample_rate != 16_000 or chunk.channels != 1:
                raise MeetingAudioStoreError("AUDIO_FORMAT_UNSUPPORTED", "PCM range format is unsupported")
            if chunk.byte_size != chunk.duration_ms * 32:
                raise MeetingAudioStoreError("AUDIO_CHUNK_SIZE_INVALID", "PCM chunk size is inconsistent")
            if chunk.start_ms > cursor_ms:
                raise MeetingAudioStoreError("AUDIO_RANGE_GAP", "PCM range contains an audio gap")
            read_start_ms = max(cursor_ms, chunk.start_ms)
            read_end_ms = min(end_ms, chunk_end)
            offset = (read_start_ms - chunk.start_ms) * 32
            length = (read_end_ms - read_start_ms) * 32
            path = self._verified_chunk_path(owner_user_id, meeting_id, chunk)
            try:
                with path.open("rb") as source:
                    source.seek(offset)
                    block = source.read(length)
            except OSError as exc:
                raise MeetingAudioStoreError("AUDIO_STORAGE_READ_FAILED", "PCM chunk is unavailable") from exc
            if len(block) != length:
                raise MeetingAudioStoreError("AUDIO_CHUNK_TRUNCATED", "PCM chunk is truncated")
            output.extend(block)
            cursor_ms = read_end_ms
            if cursor_ms >= end_ms:
                break
        if cursor_ms != end_ms or len(output) != expected_bytes:
            raise MeetingAudioStoreError("AUDIO_RANGE_GAP", "PCM range is not fully covered")
        return bytes(output)

    def pack_wav(
        self,
        owner_user_id: int,
        meeting_id: str,
        chunks: list[MeetingAudioChunk],
        *,
        total_duration_ms: int | None = None,
        force_repack: bool = False,
    ) -> Path:
        if total_duration_ms is not None and total_duration_ms < 0:
            raise MeetingAudioStoreError("AUDIO_RANGE_INVALID", "packed audio duration is invalid")
        ready = self.ready_packed_audio_path(owner_user_id, meeting_id)
        if not force_repack and ready is not None and (
            total_duration_ms is None
            or self._wav_duration_ms(ready) == total_duration_ms
        ):
            return ready
        if not chunks and total_duration_ms is None:
            raise MeetingAudioStoreError("AUDIO_NOT_AVAILABLE", "meeting audio is not available")
        if any(item.codec != "pcm_s16le" or item.sample_rate != 16_000 or item.channels != 1 for item in chunks):
            raise MeetingAudioStoreError("AUDIO_FORMAT_UNSUPPORTED", "audio chunks cannot be packed as WAV")
        verified_paths = {chunk.id: self._verified_chunk_path(owner_user_id, meeting_id, chunk) for chunk in chunks}
        target = self.packed_audio_path(owner_user_id, meeting_id)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.unlink(missing_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            current_ms = 0
            with wave.open(str(temporary), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16_000)
                for chunk in sorted(chunks, key=lambda item: (item.start_ms, item.stream_epoch, item.sequence)):
                    if chunk.start_ms > current_ms:
                        self._write_silence(output, chunk.start_ms - current_ms)
                        current_ms = chunk.start_ms
                    overlap_ms = max(0, current_ms - chunk.start_ms)
                    skip_bytes = min(chunk.byte_size, overlap_ms * 32)
                    path = verified_paths[chunk.id]
                    with path.open("rb") as source:
                        source.seek(skip_bytes)
                        for block in iter(lambda: source.read(1024 * 1024), b""):
                            output.writeframesraw(block)
                    written_ms = max(0, chunk.duration_ms - overlap_ms)
                    current_ms += written_ms
                if total_duration_ms is not None:
                    if current_ms > total_duration_ms:
                        raise MeetingAudioStoreError(
                            "AUDIO_RANGE_INVALID",
                            "audio chunks exceed the declared packed duration",
                        )
                    if current_ms < total_duration_ms:
                        self._write_silence(output, total_duration_ms - current_ms)
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        except MeetingAudioStoreError:
            temporary.unlink(missing_ok=True)
            raise
        except (OSError, wave.Error) as exc:
            temporary.unlink(missing_ok=True)
            raise MeetingAudioStoreError("AUDIO_PACK_FAILED", "meeting audio could not be packed") from exc
        return target

    @staticmethod
    def _wav_duration_ms(path: Path) -> int | None:
        try:
            with wave.open(str(path), "rb") as source:
                if source.getframerate() != 16_000:
                    return None
                return source.getnframes() * 1000 // source.getframerate()
        except (OSError, wave.Error):
            return None

    @staticmethod
    def _write_silence(output: wave.Wave_write, duration_ms: int) -> None:
        remaining = duration_ms * 32
        silence = bytes(min(1024 * 1024, remaining))
        while remaining:
            block = silence if len(silence) <= remaining else silence[:remaining]
            output.writeframesraw(block)
            remaining -= len(block)
