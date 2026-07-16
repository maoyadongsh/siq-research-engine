"""Archive generic document-parser artifacts into an immutable Deal parse run."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlsplit

import httpx

from services import deal_store
from services.path_config import DOCUMENT_PARSER_RESULTS_ROOT

ARCHIVE_SCHEMA_VERSION = "siq_primary_market_document_archive_v1"
RESULT_CONTRACT_VERSION = "document_parser_artifact_contract_v1"
TASK_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
DOCUMENT_ID_RE = re.compile(r"^DOC-[A-Z0-9]{12,32}$")
PARSE_RUN_ID_RE = re.compile(r"^PRUN-[0-9]{8}-[A-Z0-9]{12,32}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")

ARTIFACT_ALLOWLIST = frozenset(
    {
        "manifest.json",
        "document.md",
        "document_full.json",
        "blocks.json",
        "blocks.ndjson",
        "tables.json",
        "table_index.json",
        "logical_tables.json",
        "table_relations.json",
        "figures.json",
        "figure_index.json",
        "source_map.json",
        "quality_report.json",
        "layout_blocks.json",
        "reading_order.json",
        "comparison_map.json",
    }
)
REQUIRED_ARTIFACTS = frozenset(
    {
        "manifest.json",
        "document.md",
        "document_full.json",
        "blocks.json",
        "source_map.json",
        "quality_report.json",
    }
)
SUCCESS_STATUSES = frozenset({"completed", "completed_with_warnings", "succeeded", "success"})


class DocumentArtifactTransportError(RuntimeError):
    """The upstream artifact contract or archive failed validation."""


class DocumentArtifactTransportUnavailable(DocumentArtifactTransportError):
    """The upstream API could not be reached and auto mode may use local compatibility."""


@dataclass(frozen=True)
class ArtifactLimits:
    max_file_bytes: int
    max_total_bytes: int
    max_files: int
    max_contract_bytes: int

    @classmethod
    def from_env(cls) -> "ArtifactLimits":
        return cls(
            max_file_bytes=_positive_env_int(
                "SIQ_PRIMARY_MARKET_DOCUMENT_ARTIFACT_MAX_FILE_BYTES",
                128 * 1024 * 1024,
            ),
            max_total_bytes=_positive_env_int(
                "SIQ_PRIMARY_MARKET_DOCUMENT_ARTIFACT_MAX_TOTAL_BYTES",
                512 * 1024 * 1024,
            ),
            max_files=_positive_env_int(
                "SIQ_PRIMARY_MARKET_DOCUMENT_ARTIFACT_MAX_FILES",
                64,
            ),
            max_contract_bytes=_positive_env_int(
                "SIQ_PRIMARY_MARKET_DOCUMENT_ARTIFACT_MAX_CONTRACT_BYTES",
                4 * 1024 * 1024,
            ),
        )


@dataclass(frozen=True)
class ArtifactDescriptor:
    name: str
    size_bytes: int
    sha256: str


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise DocumentArtifactTransportError(f"{name} must be an integer") from exc
    if value <= 0:
        raise DocumentArtifactTransportError(f"{name} must be positive")
    return value


def artifact_transport_mode(value: str | None = None) -> str:
    mode = str(
        value
        if value is not None
        else os.environ.get("SIQ_PRIMARY_MARKET_DOCUMENT_ARTIFACT_TRANSPORT", "auto")
    ).strip().lower()
    if mode not in {"api", "auto", "shared_fs"}:
        raise DocumentArtifactTransportError(
            "SIQ_PRIMARY_MARKET_DOCUMENT_ARTIFACT_TRANSPORT must be api, auto, or shared_fs"
        )
    return mode


def parser_owner_headers(
    run: Mapping[str, Any],
    *,
    access_token: str | None = None,
) -> dict[str, str]:
    """Rebuild parser identity exclusively from the persisted submitting scope."""

    raw_scope = run.get("parser_owner_scope")
    scope = dict(raw_scope) if isinstance(raw_scope, Mapping) else {}
    submitted_by = run.get("submitted_by")
    legacy = dict(submitted_by) if isinstance(submitted_by, Mapping) else {}
    if scope:
        owner_id = str(scope.get("owner_id") or "").strip()
        tenant_id = str(scope.get("tenant_id") or "").strip()
        user_role = str(scope.get("user_role") or "").strip()
        market_scope = str(scope.get("market_scope") or "").strip().upper()
        if not all((owner_id, tenant_id, user_role, market_scope)):
            raise DocumentArtifactTransportError("parser_owner_scope_incomplete")
    else:
        owner_id = str(legacy.get("id") or "").strip()
        if not owner_id:
            raise DocumentArtifactTransportError("parser_owner_scope_missing")
        tenant_id = str(legacy.get("tenant_id") or "legacy").strip() or "legacy"
        user_role = str(
            legacy.get("user_role") or legacy.get("role") or "analyst"
        ).strip() or "analyst"
        market_scope = str(legacy.get("market_scope") or "CN").strip().upper()
    if market_scope != "CN":
        raise DocumentArtifactTransportError("parser_owner_scope_market_mismatch")
    headers = {
        "X-SIQ-User-Id": owner_id,
        "X-SIQ-Tenant-Id": tenant_id,
        "X-SIQ-User-Role": user_role,
        "X-SIQ-Market-Scope": market_scope,
    }
    token = str(access_token or "").strip()
    if token:
        headers["X-Document-Parser-Token"] = token
    return headers


def _validate_identity(
    task_id: str,
    document_id: str,
    parse_run_id: str,
) -> tuple[str, str, str]:
    normalized_task = str(task_id or "").strip()
    normalized_document = str(document_id or "").strip().upper()
    normalized_run = str(parse_run_id or "").strip().upper()
    if not TASK_ID_RE.fullmatch(normalized_task):
        raise DocumentArtifactTransportError("invalid document parser task id")
    if not DOCUMENT_ID_RE.fullmatch(normalized_document):
        raise DocumentArtifactTransportError("invalid primary-market document id")
    if not PARSE_RUN_ID_RE.fullmatch(normalized_run):
        raise DocumentArtifactTransportError("invalid primary-market parse run id")
    return normalized_task, normalized_document, normalized_run


def _validate_api_base(api_base: str) -> str:
    value = str(api_base or "").strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DocumentArtifactTransportError("invalid document parser API base")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise DocumentArtifactTransportError("document parser API base contains forbidden components")
    return value


def _validate_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    validated: dict[str, str] = {}
    for name, value in dict(headers or {}).items():
        key = str(name)
        text = str(value)
        if not key or any(char in key or char in text for char in ("\r", "\n")):
            raise DocumentArtifactTransportError("invalid document parser request header")
        validated[key] = text
    return validated


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(dict(payload), ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bundle_sha256(files: list[dict[str, Any]]) -> str:
    lines = [
        f"{item['path']}:{item['sha256']}"
        for item in sorted(files, key=lambda item: str(item["path"]))
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _write_bytes_exclusive(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def _write_json_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    _write_bytes_exclusive(path, _json_bytes(payload))


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DocumentArtifactTransportError(f"invalid {label}") from exc
    if not isinstance(payload, dict):
        raise DocumentArtifactTransportError(f"{label} must be an object")
    return payload


def _validate_json_identities(staging: Path, task_id: str) -> None:
    manifest = _load_json_object(staging / "manifest.json", "manifest.json")
    if str(manifest.get("task_id") or "") != task_id:
        raise DocumentArtifactTransportError("document parser manifest task identity mismatch")
    document_full = _load_json_object(
        staging / "document_full.json",
        "document_full.json",
    )
    if str(document_full.get("task_id") or "") != task_id:
        raise DocumentArtifactTransportError("document_full task identity mismatch")
    for name in ARTIFACT_ALLOWLIST:
        path = staging / name
        if not path.is_file() or not name.endswith(".json"):
            continue
        payload = _load_json_object(path, name)
        artifact_task_id = str(payload.get("task_id") or "")
        if artifact_task_id and artifact_task_id != task_id:
            raise DocumentArtifactTransportError(f"artifact task identity mismatch: {name}")


def _descriptor_from_payload(
    task_id: str,
    name: str,
    raw: Mapping[str, Any],
    limits: ArtifactLimits,
) -> ArtifactDescriptor:
    if str(raw.get("path") or "") != name:
        raise DocumentArtifactTransportError(f"invalid artifact path: {name}")
    expected_url = f"/api/artifact/{quote(task_id, safe='')}/{name}"
    if str(raw.get("url") or "") != expected_url:
        raise DocumentArtifactTransportError(f"invalid artifact URL: {name}")
    try:
        size_bytes = int(raw.get("size_bytes"))
    except (TypeError, ValueError) as exc:
        raise DocumentArtifactTransportError(f"invalid artifact size: {name}") from exc
    sha256 = str(raw.get("sha256") or "").lower()
    if size_bytes < 0 or size_bytes > limits.max_file_bytes:
        raise DocumentArtifactTransportError(f"artifact exceeds per-file limit: {name}")
    if not SHA256_RE.fullmatch(sha256):
        raise DocumentArtifactTransportError(f"invalid artifact SHA-256: {name}")
    return ArtifactDescriptor(name=name, size_bytes=size_bytes, sha256=sha256)


def _validate_result_contract(
    payload: Mapping[str, Any],
    task_id: str,
    limits: ArtifactLimits,
) -> tuple[list[ArtifactDescriptor], dict[str, Any]]:
    if payload.get("artifact_contract_version") != RESULT_CONTRACT_VERSION:
        raise DocumentArtifactTransportError("unsupported document parser artifact contract")
    if "markdown" in payload:
        raise DocumentArtifactTransportError("compact document parser result unexpectedly included Markdown")
    task = payload.get("task")
    if not isinstance(task, Mapping) or str(task.get("task_id") or "") != task_id:
        raise DocumentArtifactTransportError("document parser result task identity mismatch")
    status = str(task.get("status") or task.get("stage") or "").strip().lower()
    if status not in SUCCESS_STATUSES:
        raise DocumentArtifactTransportError("document parser task is not complete")
    manifest = payload.get("manifest")
    if not isinstance(manifest, Mapping) or str(manifest.get("task_id") or "") != task_id:
        raise DocumentArtifactTransportError("document parser result manifest identity mismatch")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise DocumentArtifactTransportError("document parser result omitted artifact descriptors")

    descriptors: list[ArtifactDescriptor] = []
    for name in sorted(ARTIFACT_ALLOWLIST):
        raw = artifacts.get(name)
        if not isinstance(raw, Mapping) or not raw.get("exists"):
            if name in REQUIRED_ARTIFACTS:
                raise DocumentArtifactTransportError(f"required parser artifact is missing: {name}")
            continue
        descriptors.append(_descriptor_from_payload(task_id, name, raw, limits))
    if len(descriptors) > limits.max_files:
        raise DocumentArtifactTransportError("artifact count exceeds configured limit")
    declared_total = sum(item.size_bytes for item in descriptors)
    if declared_total > limits.max_total_bytes:
        raise DocumentArtifactTransportError("artifact bundle exceeds configured total size limit")
    return descriptors, dict(manifest)


def _response_failure(response: httpx.Response, operation: str) -> None:
    code = int(response.status_code)
    if code in {408, 429} or 500 <= code <= 599:
        raise DocumentArtifactTransportUnavailable(
            f"document parser {operation} is temporarily unavailable (HTTP {code})"
        )
    raise DocumentArtifactTransportError(
        f"document parser {operation} failed (HTTP {code})"
    )


async def _result_contract(
    client: httpx.AsyncClient,
    *,
    api_base: str,
    task_id: str,
    headers: Mapping[str, str],
    max_bytes: int,
) -> dict[str, Any]:
    url = f"{api_base}/api/result/{quote(task_id, safe='')}?include_markdown=false"
    try:
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code != 200:
                _response_failure(response, "result request")
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    declared_length = int(content_length)
                except ValueError as exc:
                    raise DocumentArtifactTransportError(
                        "document parser result has invalid Content-Length"
                    ) from exc
                if declared_length < 0 or declared_length > max_bytes:
                    raise DocumentArtifactTransportError(
                        "document parser result contract exceeds configured limit"
                    )
            chunks: list[bytes] = []
            size_bytes = 0
            async for chunk in response.aiter_bytes(256 * 1024):
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise DocumentArtifactTransportError(
                        "document parser result contract exceeds configured limit"
                    )
                chunks.append(chunk)
    except httpx.RequestError as exc:
        raise DocumentArtifactTransportUnavailable(
            "document parser result API is unavailable"
        ) from exc
    try:
        payload = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DocumentArtifactTransportError("document parser result is not JSON") from exc
    if not isinstance(payload, dict):
        raise DocumentArtifactTransportError("document parser result must be an object")
    return payload


async def _download_artifact(
    client: httpx.AsyncClient,
    *,
    api_base: str,
    task_id: str,
    descriptor: ArtifactDescriptor,
    destination: Path,
    headers: Mapping[str, str],
    remaining_total: int,
) -> dict[str, Any]:
    url = (
        f"{api_base}/api/artifact/{quote(task_id, safe='')}/"
        f"{quote(descriptor.name, safe='')}?download=true"
    )
    try:
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code != 200:
                _response_failure(response, f"artifact request for {descriptor.name}")
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    declared_length = int(content_length)
                except ValueError as exc:
                    raise DocumentArtifactTransportError(
                        f"invalid Content-Length for {descriptor.name}"
                    ) from exc
                if declared_length != descriptor.size_bytes:
                    raise DocumentArtifactTransportError(
                        f"artifact Content-Length mismatch: {descriptor.name}"
                    )

            file_descriptor = os.open(
                destination,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            digest = hashlib.sha256()
            size_bytes = 0
            try:
                with os.fdopen(file_descriptor, "wb", closefd=False) as handle:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        size_bytes += len(chunk)
                        if (
                            size_bytes > descriptor.size_bytes
                            or size_bytes > remaining_total
                        ):
                            raise DocumentArtifactTransportError(
                                f"artifact size exceeded declared budget: {descriptor.name}"
                            )
                        digest.update(chunk)
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                os.close(file_descriptor)
    except httpx.RequestError as exc:
        destination.unlink(missing_ok=True)
        raise DocumentArtifactTransportUnavailable(
            f"document parser artifact is unavailable: {descriptor.name}"
        ) from exc
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    actual_sha256 = digest.hexdigest()
    if size_bytes != descriptor.size_bytes or actual_sha256 != descriptor.sha256:
        destination.unlink(missing_ok=True)
        raise DocumentArtifactTransportError(
            f"artifact integrity mismatch: {descriptor.name}"
        )
    return {
        "path": descriptor.name,
        "size_bytes": size_bytes,
        "sha256": actual_sha256,
    }


async def _stage_from_api(
    client: httpx.AsyncClient,
    staging: Path,
    *,
    api_base: str,
    task_id: str,
    headers: Mapping[str, str],
    limits: ArtifactLimits,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = await _result_contract(
        client,
        api_base=api_base,
        task_id=task_id,
        headers=headers,
        max_bytes=limits.max_contract_bytes,
    )
    descriptors, result_manifest = _validate_result_contract(payload, task_id, limits)
    copied: list[dict[str, Any]] = []
    total_bytes = 0
    for descriptor in descriptors:
        entry = await _download_artifact(
            client,
            api_base=api_base,
            task_id=task_id,
            descriptor=descriptor,
            destination=staging / descriptor.name,
            headers=headers,
            remaining_total=limits.max_total_bytes - total_bytes,
        )
        copied.append(entry)
        total_bytes += int(entry["size_bytes"])
    _validate_json_identities(staging, task_id)
    downloaded_manifest = _load_json_object(staging / "manifest.json", "manifest.json")
    if downloaded_manifest != result_manifest:
        raise DocumentArtifactTransportError(
            "document parser manifest changed during artifact download"
        )
    return copied, result_manifest


def _copy_local_file(
    source: Path,
    destination: Path,
    *,
    limits: ArtifactLimits,
    remaining_total: int,
) -> dict[str, Any]:
    if source.is_symlink():
        raise DocumentArtifactTransportError(f"unsafe local parser artifact: {source.name}")
    open_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(source, open_flags)
    except OSError as exc:
        raise DocumentArtifactTransportError(
            f"unsafe local parser artifact: {source.name}"
        ) from exc
    try:
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode):
            raise DocumentArtifactTransportError(
                f"unsafe local parser artifact: {source.name}"
            )
        size_bytes = source_stat.st_size
        if size_bytes > limits.max_file_bytes or size_bytes > remaining_total:
            raise DocumentArtifactTransportError(
                f"local artifact exceeds limit: {source.name}"
            )
        destination_descriptor = os.open(
            destination,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
    except Exception:
        os.close(source_fd)
        raise
    digest = hashlib.sha256()
    copied = 0
    try:
        with os.fdopen(source_fd, "rb") as input_handle, os.fdopen(
            destination_descriptor,
            "wb",
            closefd=False,
        ) as output_handle:
            for chunk in iter(lambda: input_handle.read(1024 * 1024), b""):
                copied += len(chunk)
                if copied > size_bytes or copied > remaining_total:
                    raise DocumentArtifactTransportError(
                        f"local artifact changed while copying: {source.name}"
                    )
                digest.update(chunk)
                output_handle.write(chunk)
            output_handle.flush()
            os.fsync(output_handle.fileno())
    finally:
        os.close(destination_descriptor)
    if copied != size_bytes:
        destination.unlink(missing_ok=True)
        raise DocumentArtifactTransportError(f"local artifact size changed: {source.name}")
    return {"path": source.name, "size_bytes": copied, "sha256": digest.hexdigest()}


def _rename_directory_noreplace(source: Path, destination: Path) -> None:
    """Atomically publish a directory without replacing an existing archive."""

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise DocumentArtifactTransportError(
            "atomic no-replace archive publication is unavailable"
        )
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(source),
        -100,
        os.fsencode(destination),
        1,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(error_number, os.strerror(error_number), destination)
    raise OSError(error_number, os.strerror(error_number), destination)


def _stage_from_shared_fs(
    staging: Path,
    *,
    task_id: str,
    results_root: Path,
    limits: ArtifactLimits,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = results_root.resolve()
    raw_result_dir = root / task_id
    if raw_result_dir.is_symlink():
        raise DocumentArtifactTransportError("local parser task directory must not be a symlink")
    result_dir = raw_result_dir.resolve()
    try:
        result_dir.relative_to(root)
    except ValueError as exc:
        raise DocumentArtifactTransportError("local parser result escapes configured root") from exc
    if not result_dir.is_dir():
        raise DocumentArtifactTransportUnavailable("local document parser result is unavailable")
    manifest = _load_json_object(result_dir / "manifest.json", "manifest.json")
    if str(manifest.get("task_id") or "") != task_id:
        raise DocumentArtifactTransportError("local parser manifest task identity mismatch")

    names = [name for name in sorted(ARTIFACT_ALLOWLIST) if (result_dir / name).is_file()]
    missing = sorted(REQUIRED_ARTIFACTS - set(names))
    if missing:
        raise DocumentArtifactTransportError(
            f"local parser result is missing required artifacts: {', '.join(missing)}"
        )
    if len(names) > limits.max_files:
        raise DocumentArtifactTransportError("local artifact count exceeds configured limit")
    copied: list[dict[str, Any]] = []
    total_bytes = 0
    for name in names:
        entry = _copy_local_file(
            result_dir / name,
            staging / name,
            limits=limits,
            remaining_total=limits.max_total_bytes - total_bytes,
        )
        copied.append(entry)
        total_bytes += int(entry["size_bytes"])
    _validate_json_identities(staging, task_id)
    return copied, manifest


def _verify_existing_archive(
    target_dir: Path,
    *,
    task_id: str,
    document_id: str,
    parse_run_id: str,
    raw_sha256: str | None = None,
    parse_config_hash: str | None = None,
) -> dict[str, Any]:
    if target_dir.is_symlink() or not target_dir.is_dir():
        raise DocumentArtifactTransportError("existing document archive is unsafe")
    archive_path = target_dir / "archive_manifest.json"
    if archive_path.is_symlink() or not archive_path.is_file():
        raise DocumentArtifactTransportError("existing archive manifest is unsafe")
    archive = _load_json_object(archive_path, "archive_manifest.json")
    if archive.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
        raise DocumentArtifactTransportError("existing document archive schema mismatch")
    if (
        str(archive.get("parser_task_id") or "") != task_id
        or str(archive.get("document_id") or "") != document_id
        or str(archive.get("parse_run_id") or "") != parse_run_id
    ):
        raise DocumentArtifactTransportError("existing document archive identity conflict")
    if raw_sha256 and archive.get("raw_sha256") != raw_sha256:
        raise DocumentArtifactTransportError("existing archive source hash conflict")
    if parse_config_hash and archive.get("parse_config_hash") != parse_config_hash:
        raise DocumentArtifactTransportError("existing archive parse config conflict")
    raw_files = archive.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise DocumentArtifactTransportError("existing document archive has no files")
    verified: list[dict[str, Any]] = []
    listed_names: set[str] = set()
    for raw in raw_files:
        if not isinstance(raw, Mapping):
            raise DocumentArtifactTransportError("existing archive file entry is invalid")
        name = str(raw.get("path") or "")
        if name not in ARTIFACT_ALLOWLIST:
            raise DocumentArtifactTransportError("existing archive contains a forbidden file")
        if name in listed_names:
            raise DocumentArtifactTransportError("existing archive contains a duplicate file")
        listed_names.add(name)
        path = target_dir / name
        if path.is_symlink() or not path.is_file():
            raise DocumentArtifactTransportError(f"existing archive is missing {name}")
        entry = {
            "path": name,
            "size_bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        if (
            raw.get("size_bytes") != entry["size_bytes"]
            or raw.get("sha256") != entry["sha256"]
        ):
            raise DocumentArtifactTransportError(f"existing archive integrity conflict: {name}")
        verified.append(entry)
    if not REQUIRED_ARTIFACTS.issubset(listed_names):
        raise DocumentArtifactTransportError("existing archive is missing required artifacts")
    actual_names = {
        path.name
        for path in target_dir.iterdir()
        if path.is_file() and path.name in ARTIFACT_ALLOWLIST
    }
    if actual_names != listed_names:
        raise DocumentArtifactTransportError("existing archive has untracked artifacts")
    if archive.get("bundle_sha256") != _bundle_sha256(verified):
        raise DocumentArtifactTransportError("existing archive bundle hash conflict")
    return archive


def _publish_archive(
    staging: Path,
    target_dir: Path,
    *,
    task_id: str,
    document_id: str,
    parse_run_id: str,
    raw_sha256: str | None,
    parse_config_hash: str | None,
) -> dict[str, Any]:
    directory_descriptor = os.open(staging, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    lock_target = target_dir.parent / f".{parse_run_id}.document-archive"
    with deal_store._locked_path(lock_target):
        if target_dir.exists():
            shutil.rmtree(staging, ignore_errors=True)
            return _verify_existing_archive(
                target_dir,
                task_id=task_id,
                document_id=document_id,
                parse_run_id=parse_run_id,
                raw_sha256=raw_sha256,
                parse_config_hash=parse_config_hash,
            )
        try:
            _rename_directory_noreplace(staging, target_dir)
        except FileExistsError:
            shutil.rmtree(staging, ignore_errors=True)
            return _verify_existing_archive(
                target_dir,
                task_id=task_id,
                document_id=document_id,
                parse_run_id=parse_run_id,
                raw_sha256=raw_sha256,
                parse_config_hash=parse_config_hash,
            )
    parent_descriptor = os.open(target_dir.parent, os.O_RDONLY)
    try:
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)
    return _load_json_object(target_dir / "archive_manifest.json", "archive_manifest.json")


async def archive_document_parser_result(
    *,
    deal_id: str,
    document_id: str,
    parse_run_id: str,
    parser_task_id: str,
    target_dir: Path,
    api_base: str,
    headers: Mapping[str, str] | None,
    mode: str | None = None,
    shared_results_root: Path | str | None = None,
    limits: ArtifactLimits | None = None,
    client: httpx.AsyncClient | None = None,
    wiki_root: Path | str | None = None,
    raw_sha256: str | None = None,
    parse_config_hash: str | None = None,
) -> dict[str, Any]:
    """Download/copy a completed task and atomically publish its immutable Deal archive."""

    task_id, normalized_document, normalized_run = _validate_identity(
        parser_task_id,
        document_id,
        parse_run_id,
    )
    normalized_deal = deal_store.validate_deal_id(deal_id)
    normalized_mode = artifact_transport_mode(mode)
    normalized_api_base = _validate_api_base(api_base)
    normalized_headers = _validate_headers(headers)
    normalized_raw_sha256 = str(raw_sha256 or "").strip().lower() or None
    normalized_parse_config_hash = str(parse_config_hash or "").strip().lower() or None
    for label, value in (
        ("raw_sha256", normalized_raw_sha256),
        ("parse_config_hash", normalized_parse_config_hash),
    ):
        if value is not None and not SHA256_RE.fullmatch(value):
            raise DocumentArtifactTransportError(f"invalid {label}")
    limits = limits or ArtifactLimits.from_env()
    target_dir = Path(target_dir).expanduser()
    deal_dir = deal_store.safe_deal_dir(normalized_deal, wiki_root=wiki_root)
    expected_target = (
        deal_dir
        / "parsed_documents"
        / normalized_document
        / "runs"
        / normalized_run
    )
    if target_dir.resolve() != expected_target.resolve():
        raise DocumentArtifactTransportError("document archive target escapes Deal namespace")
    for candidate in (
        deal_dir / "parsed_documents",
        deal_dir / "parsed_documents" / normalized_document,
        expected_target.parent,
    ):
        if candidate.is_symlink():
            raise DocumentArtifactTransportError("document archive target contains a symlink")
    target_dir = expected_target
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        archive = _verify_existing_archive(
            target_dir,
            task_id=task_id,
            document_id=normalized_document,
            parse_run_id=normalized_run,
            raw_sha256=normalized_raw_sha256,
            parse_config_hash=normalized_parse_config_hash,
        )
        return {
            "status": "existing",
            "transport": archive.get("transport"),
            "archive_dir": target_dir,
            "document_path": target_dir / "document.md",
            "archive_manifest": archive,
        }

    async def stage_api(staging: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if client is not None:
            return await _stage_from_api(
                client,
                staging,
                api_base=normalized_api_base,
                task_id=task_id,
                headers=normalized_headers,
                limits=limits,
            )
        timeout = httpx.Timeout(120.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as owned_client:
            return await _stage_from_api(
                owned_client,
                staging,
                api_base=normalized_api_base,
                task_id=task_id,
                headers=normalized_headers,
                limits=limits,
            )

    attempts = [normalized_mode]
    if normalized_mode == "auto":
        attempts = ["api", "shared_fs"]
    last_unavailable: Exception | None = None
    for attempt in attempts:
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".staging-{normalized_run}-",
                dir=target_dir.parent,
            )
        )
        try:
            if attempt == "api":
                copied, parser_manifest = await stage_api(staging)
            else:
                copied, parser_manifest = _stage_from_shared_fs(
                    staging,
                    task_id=task_id,
                    results_root=Path(shared_results_root or DOCUMENT_PARSER_RESULTS_ROOT),
                    limits=limits,
                )
            archive_manifest = {
                "schema_version": ARCHIVE_SCHEMA_VERSION,
                "deal_id": normalized_deal,
                "document_id": normalized_document,
                "parse_run_id": normalized_run,
                "parser_task_id": task_id,
                "parser_manifest_schema": parser_manifest.get("schema_version"),
                "artifact_contract_version": (
                    RESULT_CONTRACT_VERSION
                    if attempt == "api"
                    else "shared_fs_compat_v1"
                ),
                "transport": attempt,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "raw_sha256": normalized_raw_sha256,
                "parse_config_hash": normalized_parse_config_hash,
                "files": sorted(copied, key=lambda item: str(item["path"])),
                "bundle_sha256": _bundle_sha256(copied),
            }
            _write_json_exclusive(staging / "archive_manifest.json", archive_manifest)
            published = _publish_archive(
                staging,
                target_dir,
                task_id=task_id,
                document_id=normalized_document,
                parse_run_id=normalized_run,
                raw_sha256=normalized_raw_sha256,
                parse_config_hash=normalized_parse_config_hash,
            )
            return {
                "status": "archived",
                "transport": published.get("transport"),
                "archive_dir": target_dir,
                "document_path": target_dir / "document.md",
                "archive_manifest": published,
            }
        except DocumentArtifactTransportUnavailable as exc:
            shutil.rmtree(staging, ignore_errors=True)
            last_unavailable = exc
            if normalized_mode != "auto" or attempt != "api":
                raise
            continue
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    raise DocumentArtifactTransportUnavailable(
        str(last_unavailable or "document parser artifacts are unavailable")
    )


__all__ = [
    "ARCHIVE_SCHEMA_VERSION",
    "ARTIFACT_ALLOWLIST",
    "ArtifactLimits",
    "DocumentArtifactTransportError",
    "DocumentArtifactTransportUnavailable",
    "archive_document_parser_result",
    "artifact_transport_mode",
    "parser_owner_headers",
]
