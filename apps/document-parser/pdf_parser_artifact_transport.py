"""Materialize PDF parser artifacts through its authenticated HTTP API."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

STAGE_RECEIPT_NAME = ".siq_pdf_parser_stage.json"
STAGING_ROOT_RECEIPT_NAME = ".siq_pdf_parser_staging_root.json"
STAGE_RECEIPT_SCHEMA = "siq_pdf_parser_api_stage_v2"
STAGING_ROOT_RECEIPT_SCHEMA = "siq_pdf_parser_staging_root_v1"
ARTIFACT_API_CONTRACT_VERSION = "pdf_parser_artifact_api_v1"
ARTIFACT_MANIFEST_SCHEMA = "pdf_parser_artifact_manifest_v1"
HASH_MANIFEST_SCHEMA = "pdf_parser_hash_manifest_v1"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
IMAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,239}\.(?:png|jpe?g|webp)$", re.IGNORECASE)

REQUIRED_ARTIFACTS = (
    "result.md",
    "result_complete.md",
    "document_full.json",
    "content_list_enhanced.json",
    "table_index.json",
    "table_relations.json",
    "financial_data.json",
    "financial_checks.json",
    "quality_report.json",
    "content_list.json",
)
FETCHABLE_OPTIONAL_ARTIFACTS = ("middle.json", "model_output.json")
HASH_MANIFEST_ARTIFACTS = REQUIRED_ARTIFACTS + FETCHABLE_OPTIONAL_ARTIFACTS + (
    "result_payload_summary.json",
    "corrections.json",
)
API_FILE_ARTIFACTS = REQUIRED_ARTIFACTS + FETCHABLE_OPTIONAL_ARTIFACTS + (
    "metadata.json",
    "artifact_manifest.json",
    "hash_manifest.json",
)
RESULT_PAYLOAD_ARTIFACTS = frozenset(REQUIRED_ARTIFACTS + FETCHABLE_OPTIONAL_ARTIFACTS + ("images",))


class ArtifactTransportError(RuntimeError):
    """Raised when an upstream artifact bundle cannot be trusted or staged."""


@dataclass(frozen=True)
class DownloadInfo:
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ArtifactTransportLimits:
    max_file_bytes: int
    max_total_bytes: int
    max_files: int
    max_json_bytes: int

    @classmethod
    def from_env(cls) -> "ArtifactTransportLimits":
        return cls(
            max_file_bytes=_positive_env_int(
                "SIQ_DOCUMENT_PARSE_PDF_STAGE_MAX_FILE_BYTES", 128 * 1024 * 1024
            ),
            max_total_bytes=_positive_env_int(
                "SIQ_DOCUMENT_PARSE_PDF_STAGE_MAX_TOTAL_BYTES", 1024 * 1024 * 1024
            ),
            max_files=_positive_env_int("SIQ_DOCUMENT_PARSE_PDF_STAGE_MAX_FILES", 4096),
            max_json_bytes=_positive_env_int(
                "SIQ_DOCUMENT_PARSE_PDF_STAGE_MAX_JSON_BYTES", 16 * 1024 * 1024
            ),
        )


@dataclass(frozen=True)
class StagedArtifactBundle:
    result_dir: Path
    task_id: str
    bundle_sha256: str
    file_count: int
    total_bytes: int
    mode: str = "api"


FetchToPath = Callable[[str, Path, Mapping[str, str], int], DownloadInfo]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_transport_mode() -> str:
    mode = os.environ.get("SIQ_DOCUMENT_PARSE_PDF_ARTIFACT_TRANSPORT", "auto").strip().lower()
    if mode not in {"auto", "api", "shared_fs"}:
        raise ArtifactTransportError(
            "SIQ_DOCUMENT_PARSE_PDF_ARTIFACT_TRANSPORT must be auto, api, or shared_fs"
        )
    return mode


def artifact_transport_status() -> dict[str, Any]:
    configured_mode = os.environ.get(
        "SIQ_DOCUMENT_PARSE_PDF_ARTIFACT_TRANSPORT",
        "auto",
    ).strip().lower()
    try:
        mode = artifact_transport_mode()
        limits = ArtifactTransportLimits.from_env()
    except ArtifactTransportError as exc:
        return {
            "ready": False,
            "configured_mode": configured_mode,
            "contract_version": ARTIFACT_API_CONTRACT_VERSION,
            "error": str(exc),
        }
    return {
        "ready": True,
        "configured_mode": mode,
        "contract_version": ARTIFACT_API_CONTRACT_VERSION,
        "limits": {
            "max_file_bytes": limits.max_file_bytes,
            "max_total_bytes": limits.max_total_bytes,
            "max_files": limits.max_files,
            "max_json_bytes": limits.max_json_bytes,
        },
    }


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ArtifactTransportError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ArtifactTransportError(f"{name} must be positive")
    return value


def _validate_task_id(task_id: str) -> str:
    value = str(task_id or "")
    if value in {".", ".."} or not TASK_ID_RE.fullmatch(value):
        raise ArtifactTransportError("Invalid PDF parser task id")
    return value


def _root_fingerprint(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def _ensure_staging_root(staging_root: Path) -> Path:
    raw_root = Path(staging_root).expanduser()
    if raw_root.is_symlink():
        raise ArtifactTransportError("PDF parser staging root must not be a symlink")
    raw_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = raw_root.resolve()
    os.chmod(root, 0o700)
    marker_path = root / STAGING_ROOT_RECEIPT_NAME
    expected = {
        "schema_version": STAGING_ROOT_RECEIPT_SCHEMA,
        "root_fingerprint": _root_fingerprint(root),
    }
    if marker_path.exists():
        if marker_path.is_symlink() or not marker_path.is_file():
            raise ArtifactTransportError("PDF parser staging root receipt is unsafe")
        marker = _load_json(marker_path, label="staging root receipt")
        if any(marker.get(key) != value for key, value in expected.items()):
            raise ArtifactTransportError("PDF parser staging root receipt is invalid")
    else:
        payload = {**expected, "created_at": _utc_now_iso()}
        try:
            descriptor = os.open(
                marker_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError:
            return _ensure_staging_root(root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
                json.dump(payload, handle, ensure_ascii=True, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            os.close(descriptor)
    return root


@contextmanager
def _task_stage_lock(staging_root: Path, task_id: str):
    lock_path = staging_root / f".{task_id}.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ArtifactTransportError("PDF parser staging lock is unavailable") from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _validate_api_base(api_base: str) -> str:
    value = str(api_base or "").rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ArtifactTransportError("Invalid PDF parser API base URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ArtifactTransportError("PDF parser API base URL contains forbidden components")
    return value


def _artifact_url(api_base: str, task_id: str, artifact_name: str) -> str:
    encoded_task = urllib.parse.quote(task_id, safe="")
    encoded_name = "/".join(urllib.parse.quote(part, safe="") for part in artifact_name.split("/"))
    return f"{api_base}/api/artifact/{encoded_task}/{encoded_name}"


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201
        return None


def fetch_to_path(
    url: str,
    destination: Path,
    headers: Mapping[str, str],
    max_bytes: int,
) -> DownloadInfo:
    request_headers = {"Accept": "application/octet-stream"}
    for name, value in dict(headers or {}).items():
        if "\r" in str(name) or "\n" in str(name) or "\r" in str(value) or "\n" in str(value):
            raise ArtifactTransportError("Invalid PDF parser request header")
        request_headers[str(name)] = str(value)
    request = urllib.request.Request(url, headers=request_headers, method="GET")
    opener = urllib.request.build_opener(_NoRedirectHandler())
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    try:
        with opener.open(request, timeout=120) as response:
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise ArtifactTransportError("Invalid upstream Content-Length") from exc
                if declared_size < 0 or declared_size > max_bytes:
                    raise ArtifactTransportError("PDF parser artifact exceeds configured size limit")
            with destination.open("xb") as outfile:
                os.chmod(destination, 0o600)
                while True:
                    chunk = response.read(min(1024 * 1024, max_bytes + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ArtifactTransportError("PDF parser artifact exceeds configured size limit")
                    digest.update(chunk)
                    outfile.write(chunk)
    except urllib.error.HTTPError as exc:
        raise ArtifactTransportError(f"PDF parser artifact request failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ArtifactTransportError(f"PDF parser artifact request failed: {exc.reason}") from exc
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return DownloadInfo(size_bytes=size, sha256=digest.hexdigest())


class _DownloadBudget:
    def __init__(self, limits: ArtifactTransportLimits):
        self.limits = limits
        self.file_count = 0
        self.total_bytes = 0

    def download(
        self,
        *,
        url: str,
        destination: Path,
        headers: Mapping[str, str],
        fetcher: FetchToPath,
        max_bytes: int | None = None,
    ) -> DownloadInfo:
        if self.file_count >= self.limits.max_files:
            raise ArtifactTransportError("PDF parser artifact count exceeds configured limit")
        remaining = self.limits.max_total_bytes - self.total_bytes
        if remaining <= 0:
            raise ArtifactTransportError("PDF parser artifact bundle exceeds configured total size limit")
        allowed = min(max_bytes or self.limits.max_file_bytes, self.limits.max_file_bytes, remaining)
        info = fetcher(url, destination, headers, allowed)
        if info.size_bytes < 0 or info.size_bytes > allowed:
            destination.unlink(missing_ok=True)
            raise ArtifactTransportError("PDF parser artifact downloader violated size limit")
        if not SHA256_RE.fullmatch(str(info.sha256 or "")):
            destination.unlink(missing_ok=True)
            raise ArtifactTransportError("PDF parser artifact downloader returned invalid SHA-256")
        self.file_count += 1
        self.total_bytes += info.size_bytes
        return info


def _load_json_value(path: Path, *, label: str) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactTransportError(f"Invalid {label} JSON") from exc
    return payload


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    payload = _load_json_value(path, label=label)
    if not isinstance(payload, dict):
        raise ArtifactTransportError(f"{label} must be a JSON object")
    return payload


def _safe_metadata_filename(value: Any) -> str:
    filename = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    filename = re.sub(r"[\x00\r\n]", "_", filename)[:240]
    return filename or "document.pdf"


def _sanitize_downloaded_metadata(path: Path, *, task_id: str) -> dict[str, Any]:
    metadata = _load_json(path, label="metadata.json")
    if str(metadata.get("task_id") or "") != task_id:
        raise ArtifactTransportError("PDF parser metadata task identity mismatch")
    raw_task = metadata.get("task") if isinstance(metadata.get("task"), Mapping) else {}
    filename = _safe_metadata_filename(
        raw_task.get("filename") or metadata.get("filename") or metadata.get("result_file")
    )
    sanitized: dict[str, Any] = {
        "schema_version": "siq_pdf_parser_staged_metadata_v1",
        "source_schema_version": str(metadata.get("schema_version") or "")[:96],
        "task_id": task_id,
        "filename": filename,
        "task": {"task_id": task_id, "filename": filename},
    }
    for key in (
        "market",
        "document_profile",
        "parse_config_hash",
        "raw_sha256",
        "mineru_version",
    ):
        value = metadata.get(key)
        if isinstance(value, (str, int, float, bool)) and str(value):
            sanitized[key] = value
    raw_parser = metadata.get("parser") if isinstance(metadata.get("parser"), Mapping) else {}
    parser = {
        key: value
        for key in ("version", "backend", "parse_method")
        if isinstance((value := raw_parser.get(key)), (str, int, float, bool))
    }
    if parser:
        sanitized["parser"] = parser
    temporary = path.with_name(f".{path.name}.sanitized-{os.getpid()}")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            json.dump(sanitized, handle, ensure_ascii=True, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return sanitized


def _validate_result_payload(task_id: str, payload: Mapping[str, Any]) -> bool:
    artifacts = payload.get("artifacts") if isinstance(payload, Mapping) else None
    if not isinstance(artifacts, Mapping):
        raise ArtifactTransportError("PDF parser result payload is missing artifacts")
    unknown = set(str(name) for name in artifacts) - RESULT_PAYLOAD_ARTIFACTS
    if unknown:
        raise ArtifactTransportError("PDF parser result payload contains forbidden artifact names")
    images_expected = False
    for name, descriptor in artifacts.items():
        if not isinstance(descriptor, Mapping):
            raise ArtifactTransportError(f"Invalid result artifact descriptor: {name}")
        if not descriptor.get("exists"):
            continue
        expected_path = f"/api/artifact/{task_id}/{name}"
        if str(descriptor.get("url") or "") != expected_path:
            raise ArtifactTransportError(f"Invalid result artifact URL: {name}")
        if name == "images":
            images_expected = True
    return images_expected


def _validate_manifests(
    task_id: str,
    artifact_manifest: Mapping[str, Any],
    hash_manifest: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], str]:
    if artifact_manifest.get("schema_version") != ARTIFACT_MANIFEST_SCHEMA:
        raise ArtifactTransportError("Unsupported PDF parser artifact manifest schema")
    if hash_manifest.get("schema_version") != HASH_MANIFEST_SCHEMA:
        raise ArtifactTransportError("Unsupported PDF parser hash manifest schema")
    if str(artifact_manifest.get("task_id") or "") != task_id:
        raise ArtifactTransportError("PDF parser artifact manifest task identity mismatch")
    if str(hash_manifest.get("task_id") or "") != task_id:
        raise ArtifactTransportError("PDF parser hash manifest task identity mismatch")
    if hash_manifest.get("algorithm") != "sha256":
        raise ArtifactTransportError("Unsupported PDF parser artifact hash algorithm")

    raw_entries = hash_manifest.get("entries")
    if not isinstance(raw_entries, list):
        raise ArtifactTransportError("PDF parser hash manifest entries are invalid")
    entries: dict[str, dict[str, Any]] = {}
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise ArtifactTransportError("PDF parser hash manifest entry is invalid")
        name = str(raw.get("name") or "")
        if name not in HASH_MANIFEST_ARTIFACTS or name in entries:
            raise ArtifactTransportError("PDF parser hash manifest contains a forbidden or duplicate name")
        sha256 = str(raw.get("sha256") or "").lower()
        try:
            size_bytes = int(raw.get("size_bytes"))
        except (TypeError, ValueError) as exc:
            raise ArtifactTransportError(f"Invalid artifact size in hash manifest: {name}") from exc
        if not SHA256_RE.fullmatch(sha256) or size_bytes < 0:
            raise ArtifactTransportError(f"Invalid artifact hash metadata: {name}")
        entries[name] = {"sha256": sha256, "size_bytes": size_bytes}

    manifest_artifacts = artifact_manifest.get("artifacts")
    if not isinstance(manifest_artifacts, Mapping):
        raise ArtifactTransportError("PDF parser artifact manifest artifacts are invalid")
    unknown_manifest_names = set(str(name) for name in manifest_artifacts) - set(HASH_MANIFEST_ARTIFACTS)
    if unknown_manifest_names:
        raise ArtifactTransportError("PDF parser artifact manifest contains forbidden artifact names")
    for name in REQUIRED_ARTIFACTS:
        descriptor = manifest_artifacts.get(name)
        entry = entries.get(name)
        if not isinstance(descriptor, Mapping) or not descriptor.get("exists") or entry is None:
            raise ArtifactTransportError(f"PDF parser artifact bundle is missing required artifact: {name}")
        if str(descriptor.get("sha256") or "").lower() != entry["sha256"]:
            raise ArtifactTransportError(f"PDF parser manifest hash disagreement: {name}")
        try:
            manifest_size = int(descriptor.get("size_bytes"))
        except (TypeError, ValueError) as exc:
            raise ArtifactTransportError(f"PDF parser manifest size is invalid: {name}") from exc
        if manifest_size != entry["size_bytes"]:
            raise ArtifactTransportError(f"PDF parser manifest size disagreement: {name}")

    bundle_payload = "\n".join(
        f"{name}:{entries[name]['sha256']}" for name in REQUIRED_ARTIFACTS
    ).encode("utf-8")
    bundle_sha256 = hashlib.sha256(bundle_payload).hexdigest()
    artifact_bundle = str((artifact_manifest.get("core") or {}).get("bundle_sha256") or "").lower()
    hash_bundle = str(hash_manifest.get("bundle_sha256") or "").lower()
    if bundle_sha256 != artifact_bundle or bundle_sha256 != hash_bundle:
        raise ArtifactTransportError("PDF parser artifact bundle hash mismatch")
    return entries, bundle_sha256


def _validate_downloaded_json_artifacts(result_dir: Path, *, task_id: str) -> None:
    for name in REQUIRED_ARTIFACTS + FETCHABLE_OPTIONAL_ARTIFACTS + ("metadata.json",):
        if not name.endswith(".json") or not (result_dir / name).exists():
            continue
        _load_json_value(result_dir / name, label=name)

    metadata = _load_json(result_dir / "metadata.json", label="metadata.json")
    if str(metadata.get("task_id") or "") != task_id:
        raise ArtifactTransportError("PDF parser metadata task identity mismatch")

    content_list = _load_json_value(result_dir / "content_list.json", label="content_list.json")
    if isinstance(content_list, Mapping):
        values = (
            content_list.get("content_list")
            or content_list.get("items")
            or content_list.get("blocks")
            or []
        )
    else:
        values = content_list
    if isinstance(values, list):
        for item in values:
            if not isinstance(item, Mapping):
                continue
            for key in ("img_path", "image_path", "source_image_path"):
                raw_path = str(item.get(key) or "").replace("\\", "/")
                if not raw_path:
                    continue
                path = Path(raw_path)
                if path.is_absolute() or ".." in path.parts or path.parts[0] != "images":
                    raise ArtifactTransportError("PDF parser content list contains an unsafe image path")


def _validate_image_magic(path: Path) -> None:
    with path.open("rb") as infile:
        header = infile.read(16)
    suffix = path.suffix.lower()
    valid = (
        suffix == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n")
        or suffix in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff")
        or suffix == ".webp" and header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    )
    if not valid:
        raise ArtifactTransportError(f"Invalid image payload: {path.name}")


def _write_receipt(
    result_dir: Path,
    *,
    task_id: str,
    bundle_sha256: str,
    budget: _DownloadBudget,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(result_dir.rglob("*")):
        if path.is_symlink():
            raise ArtifactTransportError("PDF parser staged bundle contains a symlink")
        if not path.is_file() or path.name == STAGE_RECEIPT_NAME:
            continue
        relative = path.relative_to(result_dir).as_posix()
        files.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    stage_payload = "\n".join(
        f"{item['path']}:{item['size_bytes']}:{item['sha256']}" for item in files
    ).encode("utf-8")
    payload = {
        "schema_version": STAGE_RECEIPT_SCHEMA,
        "task_id": task_id,
        "transport": "api",
        "bundle_sha256": bundle_sha256,
        "stage_sha256": hashlib.sha256(stage_payload).hexdigest(),
        "file_count": len(files),
        "total_bytes": sum(int(item["size_bytes"]) for item in files),
        "download_file_count": budget.file_count,
        "download_total_bytes": budget.total_bytes,
        "files": files,
        "created_at": _utc_now_iso(),
    }
    receipt_path = result_dir / STAGE_RECEIPT_NAME
    with receipt_path.open("x", encoding="utf-8") as handle:
        os.chmod(receipt_path, 0o600)
        json.dump(payload, handle, ensure_ascii=True, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return payload


def _validate_staged_directory(
    result_dir: Path,
    *,
    task_id: str,
    expected_bundle_sha256: str | None = None,
) -> dict[str, Any]:
    if result_dir.is_symlink() or not result_dir.is_dir():
        raise ArtifactTransportError("PDF parser staged directory is unsafe")
    receipt_path = result_dir / STAGE_RECEIPT_NAME
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ArtifactTransportError("PDF parser staged receipt is missing")
    receipt = _load_json(receipt_path, label="staging receipt")
    if (
        receipt.get("schema_version") != STAGE_RECEIPT_SCHEMA
        or str(receipt.get("task_id") or "") != task_id
        or receipt.get("transport") != "api"
    ):
        raise ArtifactTransportError("PDF parser staged receipt is invalid")
    if expected_bundle_sha256 and receipt.get("bundle_sha256") != expected_bundle_sha256:
        raise ArtifactTransportError("PDF parser staged core bundle changed")
    raw_files = receipt.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ArtifactTransportError("PDF parser staged receipt has no files")
    verified: list[dict[str, Any]] = []
    listed: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            raise ArtifactTransportError("PDF parser staged file receipt is invalid")
        relative = str(raw.get("path") or "")
        relative_path = Path(relative)
        if (
            not relative
            or relative_path.is_absolute()
            or ".." in relative_path.parts
            or relative in listed
        ):
            raise ArtifactTransportError("PDF parser staged file path is unsafe")
        listed.add(relative)
        path = result_dir / relative_path
        if path.is_symlink() or not path.is_file():
            raise ArtifactTransportError(f"PDF parser staged file is missing: {relative}")
        entry = {
            "path": relative,
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        if raw.get("size_bytes") != entry["size_bytes"] or raw.get("sha256") != entry["sha256"]:
            raise ArtifactTransportError(f"PDF parser staged file integrity mismatch: {relative}")
        verified.append(entry)
    actual = {
        path.relative_to(result_dir).as_posix()
        for path in result_dir.rglob("*")
        if path.is_file() and path.name != STAGE_RECEIPT_NAME
    }
    if actual != listed:
        raise ArtifactTransportError("PDF parser staged directory has untracked files")
    stage_payload = "\n".join(
        f"{item['path']}:{item['size_bytes']}:{item['sha256']}" for item in verified
    ).encode("utf-8")
    if receipt.get("stage_sha256") != hashlib.sha256(stage_payload).hexdigest():
        raise ArtifactTransportError("PDF parser staged bundle hash mismatch")
    try:
        file_count = int(receipt.get("file_count"))
        total_bytes = int(receipt.get("total_bytes"))
    except (TypeError, ValueError) as exc:
        raise ArtifactTransportError("PDF parser staged size receipt is invalid") from exc
    if file_count != len(verified):
        raise ArtifactTransportError("PDF parser staged file count mismatch")
    if total_bytes != sum(int(item["size_bytes"]) for item in verified):
        raise ArtifactTransportError("PDF parser staged total size mismatch")
    return receipt


def _cleanup_task_stage_residues(staging_root: Path, task_id: str) -> None:
    for pattern in (f".{task_id}.tmp-*", f".{task_id}.quarantine-*"):
        for residue in staging_root.glob(pattern):
            if residue.parent != staging_root:
                continue
            if residue.is_symlink() or not residue.is_dir():
                residue.unlink(missing_ok=True)
            else:
                shutil.rmtree(residue, ignore_errors=True)


def _publish_staged_directory(
    tmp_dir: Path,
    final_dir: Path,
    *,
    task_id: str,
    bundle_sha256: str,
) -> dict[str, Any]:
    raw_final_dir = final_dir.parent / task_id
    if raw_final_dir != final_dir or raw_final_dir.is_symlink():
        raise ArtifactTransportError("PDF parser staging target must not be a symlink")
    if final_dir.exists() and not final_dir.is_dir():
        raise ArtifactTransportError("PDF parser staging target must be a directory")
    quarantine: Path | None = None
    if final_dir.exists():
        try:
            return _validate_staged_directory(
                final_dir,
                task_id=task_id,
                expected_bundle_sha256=bundle_sha256,
            )
        except ArtifactTransportError:
            quarantine = final_dir.with_name(
                f".{final_dir.name}.quarantine-{os.getpid()}-{uuid.uuid4().hex[:12]}"
            )
            os.replace(final_dir, quarantine)
    try:
        os.replace(tmp_dir, final_dir)
        receipt = _validate_staged_directory(
            final_dir,
            task_id=task_id,
            expected_bundle_sha256=bundle_sha256,
        )
    except Exception:
        if quarantine is not None and quarantine.exists():
            if final_dir.is_symlink() or final_dir.is_file():
                final_dir.unlink(missing_ok=True)
            elif final_dir.exists():
                shutil.rmtree(final_dir, ignore_errors=True)
            os.replace(quarantine, final_dir)
        raise
    if quarantine is not None:
        shutil.rmtree(quarantine, ignore_errors=True)
    for residue in final_dir.parent.glob(f".{task_id}.quarantine-*"):
        if residue != final_dir and residue.is_dir() and not residue.is_symlink():
            shutil.rmtree(residue, ignore_errors=True)
    return receipt


def stage_pdf_parser_artifacts(
    *,
    task_id: str,
    result_payload: Mapping[str, Any],
    api_base: str,
    headers: Mapping[str, str] | None,
    staging_root: Path,
    limits: ArtifactTransportLimits | None = None,
    fetcher: FetchToPath | None = None,
) -> StagedArtifactBundle:
    task_id = _validate_task_id(task_id)
    api_base = _validate_api_base(api_base)
    limits = limits or ArtifactTransportLimits.from_env()
    fetcher = fetcher or fetch_to_path
    headers = dict(headers or {})
    images_expected = _validate_result_payload(task_id, result_payload)

    staging_root = _ensure_staging_root(Path(staging_root))
    final_dir = staging_root / task_id
    if final_dir.is_symlink() or final_dir.parent != staging_root:
        raise ArtifactTransportError("PDF parser staging path escaped its private root")

    with _task_stage_lock(staging_root, task_id):
        _cleanup_task_stage_residues(staging_root, task_id)
        if final_dir.exists():
            try:
                receipt = _validate_staged_directory(final_dir, task_id=task_id)
                return StagedArtifactBundle(
                    result_dir=final_dir,
                    task_id=task_id,
                    bundle_sha256=str(receipt["bundle_sha256"]),
                    file_count=int(receipt["file_count"]),
                    total_bytes=int(receipt["total_bytes"]),
                )
            except ArtifactTransportError:
                pass

        tmp_dir = Path(tempfile.mkdtemp(prefix=f".{task_id}.tmp-", dir=staging_root))
        os.chmod(tmp_dir, 0o700)
        budget = _DownloadBudget(limits)
        try:
            budget.download(
                url=_artifact_url(api_base, task_id, "artifact_manifest.json"),
                destination=tmp_dir / "artifact_manifest.json",
                headers=headers,
                fetcher=fetcher,
                max_bytes=limits.max_json_bytes,
            )
            budget.download(
                url=_artifact_url(api_base, task_id, "hash_manifest.json"),
                destination=tmp_dir / "hash_manifest.json",
                headers=headers,
                fetcher=fetcher,
                max_bytes=limits.max_json_bytes,
            )
            artifact_manifest = _load_json(tmp_dir / "artifact_manifest.json", label="artifact manifest")
            hash_manifest = _load_json(tmp_dir / "hash_manifest.json", label="hash manifest")
            entries, bundle_sha256 = _validate_manifests(task_id, artifact_manifest, hash_manifest)

            names_to_fetch = list(REQUIRED_ARTIFACTS)
            names_to_fetch.extend(name for name in FETCHABLE_OPTIONAL_ARTIFACTS if name in entries)
            for name in names_to_fetch:
                entry = entries[name]
                if entry["size_bytes"] > limits.max_file_bytes:
                    raise ArtifactTransportError(f"PDF parser artifact exceeds configured size limit: {name}")
                info = budget.download(
                    url=_artifact_url(api_base, task_id, name),
                    destination=tmp_dir / name,
                    headers=headers,
                    fetcher=fetcher,
                )
                if info.size_bytes != entry["size_bytes"] or info.sha256 != entry["sha256"]:
                    raise ArtifactTransportError(f"PDF parser artifact hash or size mismatch: {name}")

            budget.download(
                url=_artifact_url(api_base, task_id, "metadata.json"),
                destination=tmp_dir / "metadata.json",
                headers=headers,
                fetcher=fetcher,
                max_bytes=limits.max_json_bytes,
            )
            _sanitize_downloaded_metadata(tmp_dir / "metadata.json", task_id=task_id)

            if images_expected:
                image_index_path = tmp_dir / ".images.json"
                budget.download(
                    url=_artifact_url(api_base, task_id, "images"),
                    destination=image_index_path,
                    headers=headers,
                    fetcher=fetcher,
                    max_bytes=limits.max_json_bytes,
                )
                image_index = _load_json(image_index_path, label="image index")
                if str(image_index.get("task_id") or "") != task_id:
                    raise ArtifactTransportError("PDF parser image index task identity mismatch")
                images = image_index.get("images")
                if not isinstance(images, list) or int(image_index.get("count") or 0) != len(images):
                    raise ArtifactTransportError("PDF parser image index is invalid")
                seen_images: set[str] = set()
                for item in images:
                    if not isinstance(item, Mapping):
                        raise ArtifactTransportError("PDF parser image descriptor is invalid")
                    name = str(item.get("name") or "")
                    sha256 = str(item.get("sha256") or "").lower()
                    try:
                        size_bytes = int(item.get("size_bytes"))
                    except (TypeError, ValueError) as exc:
                        raise ArtifactTransportError("PDF parser image size is invalid") from exc
                    if (
                        not IMAGE_NAME_RE.fullmatch(name)
                        or Path(name).name != name
                        or name in seen_images
                        or not SHA256_RE.fullmatch(sha256)
                        or size_bytes < 0
                    ):
                        raise ArtifactTransportError("PDF parser image descriptor is unsafe")
                    seen_images.add(name)
                    info = budget.download(
                        url=_artifact_url(api_base, task_id, f"images/{name}"),
                        destination=tmp_dir / "images" / name,
                        headers=headers,
                        fetcher=fetcher,
                    )
                    if info.size_bytes != size_bytes or info.sha256 != sha256:
                        raise ArtifactTransportError(f"PDF parser image hash or size mismatch: {name}")
                    _validate_image_magic(tmp_dir / "images" / name)
                image_index_path.unlink(missing_ok=True)

            _validate_downloaded_json_artifacts(tmp_dir, task_id=task_id)
            _write_receipt(
                tmp_dir,
                task_id=task_id,
                bundle_sha256=bundle_sha256,
                budget=budget,
            )
            receipt = _publish_staged_directory(
                tmp_dir,
                final_dir,
                task_id=task_id,
                bundle_sha256=bundle_sha256,
            )
            return StagedArtifactBundle(
                result_dir=final_dir,
                task_id=task_id,
                bundle_sha256=bundle_sha256,
                file_count=int(receipt["file_count"]),
                total_bytes=int(receipt["total_bytes"]),
            )
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise


def cleanup_staged_pdf_parser_artifacts(
    result_dir: Path,
    *,
    task_id: str,
    staging_root: Path | None = None,
) -> bool:
    task_id = _validate_task_id(task_id)
    if staging_root is None:
        return False
    try:
        trusted_root = _ensure_staging_root(Path(staging_root))
    except ArtifactTransportError:
        return False
    raw_result_dir = Path(result_dir).expanduser()
    if raw_result_dir.is_symlink():
        return False
    resolved_result = raw_result_dir.resolve()
    if resolved_result != trusted_root / task_id:
        return False
    with _task_stage_lock(trusted_root, task_id):
        try:
            _validate_staged_directory(resolved_result, task_id=task_id)
        except ArtifactTransportError:
            return False
        shutil.rmtree(resolved_result)
        return True


__all__ = [
    "ARTIFACT_API_CONTRACT_VERSION",
    "ArtifactTransportError",
    "ArtifactTransportLimits",
    "DownloadInfo",
    "StagedArtifactBundle",
    "artifact_transport_mode",
    "artifact_transport_status",
    "cleanup_staged_pdf_parser_artifacts",
    "fetch_to_path",
    "stage_pdf_parser_artifacts",
]
