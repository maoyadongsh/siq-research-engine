"""Deal data-room document storage helpers."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import quote

from services import deal_store, primary_market_wiki
from services.path_config import DOCUMENT_PARSER_RESULTS_ROOT

DEAL_DOCUMENT_SCHEMA = "siq_deal_document_v2"
DOCUMENT_ID_RE = re.compile(r"^DOC-[A-Z0-9]{12,32}$")
PARSER_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,127}$")
DEFAULT_MAX_UPLOAD_BYTES = int(os.environ.get("SIQ_DEAL_DOCUMENT_MAX_BYTES", str(200 * 1024 * 1024)))
CHUNK_SIZE = 1024 * 1024


def new_document_id() -> str:
    return f"DOC-{uuid.uuid4().hex[:16].upper()}"


def validate_document_id(document_id: str) -> str:
    normalized = str(document_id or "").strip().upper()
    if not DOCUMENT_ID_RE.fullmatch(normalized):
        raise ValueError("document_id must be DOC- followed by 12-32 uppercase hex chars")
    return normalized


def validate_parser_task_id(task_id: str) -> str:
    normalized = str(task_id or "").strip()
    if not PARSER_TASK_ID_RE.fullmatch(normalized):
        raise ValueError("task_id must be 2-128 chars of letters, numbers, underscore, dash, dot, or colon")
    return normalized


def _safe_artifact_path(value: str | None) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("artifact_path must be a relative parser artifact path")
    cleaned = path.as_posix().strip("/")
    return cleaned[:300]


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    deal_store.ensure_deal_package_dirs(package_dir)
    return package_dir


def _safe_relative_path(package_dir: Path, relative_path: str) -> Path:
    candidate = package_dir / relative_path
    target = candidate.resolve()
    try:
        target.relative_to(package_dir.resolve())
    except ValueError as exc:
        raise ValueError("document path escapes deal package") from exc
    return candidate


def _filename_extension(filename: str | None) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if not suffix:
        return ".bin"
    if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
        return ".bin"
    return suffix


def _safe_original_filename(filename: str | None) -> str:
    raw = str(filename or "document").strip() or "document"
    basename = re.split(r"[\\/]+", raw)[-1].strip() or "document"
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", basename).strip()
    return cleaned[:180] or "document"


def _clean_text(value: str | None, *, max_len: int = 256) -> str:
    return str(value or "").strip()[:max_len]


def _metadata_path(package_dir: Path, document_id: str) -> Path:
    return package_dir / "data_room" / "metadata" / f"{document_id}.json"


def _read_document_metadata(path: Path) -> dict[str, Any] | None:
    payload = deal_store.read_json(path, None)
    return payload if isinstance(payload, dict) else None


def _write_document_metadata(package_dir: Path, metadata: dict[str, Any]) -> None:
    deal_store.write_json(_metadata_path(package_dir, str(metadata["document_id"])), metadata)


def _manifest_documents(package_dir: Path) -> list[dict[str, Any]]:
    manifest = deal_store.read_json(package_dir / "manifest.json", {}) or {}
    documents = manifest.get("documents")
    return documents if isinstance(documents, list) else []


def _sync_manifest_documents(package_dir: Path) -> None:
    manifest = deal_store.read_json(package_dir / "manifest.json", {}) or {}
    documents = list_deal_documents(package_dir.name, wiki_root=package_dir.parents[1])
    manifest["documents"] = [
        {
            "document_id": item["document_id"],
            "original_filename": item.get("original_filename"),
            "storage_path": item.get("storage_path"),
            "metadata_path": item.get("metadata_path"),
            "content_type": item.get("content_type"),
            "size_bytes": item.get("size_bytes"),
            "sha256": item.get("sha256"),
            "document_type": item.get("document_type"),
            "status": item.get("status"),
            "document_status": item.get("document_status"),
            "parse_status": item.get("parse_status"),
            "analysis_source_status": item.get("analysis_source_status"),
            "index_status": item.get("index_status"),
            "parse_task_id": item.get("parse_task_id"),
            "parsed_artifact_path": item.get("parsed_artifact_path"),
            "parser_page_url": item.get("parser_page_url"),
            "current_parse_run_id": item.get("current_parse_run_id"),
            "wiki_status": item.get("wiki_status"),
            "wiki_path": item.get("wiki_path"),
            "wiki_sha256": item.get("wiki_sha256"),
            "created_at": item.get("created_at"),
        }
        for item in documents
    ]
    manifest["updated_at"] = deal_store.utc_now_iso()
    deal_store.write_json(package_dir / "manifest.json", manifest)


def _redact_document(metadata: dict[str, Any]) -> dict[str, Any]:
    return deal_store.redact_public_payload(metadata)


def list_deal_documents(deal_id: str, *, wiki_root: Path | str | None = None) -> list[dict[str, Any]]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    metadata_dir = package_dir / "data_room" / "metadata"
    if not metadata_dir.exists():
        return []
    documents: list[dict[str, Any]] = []
    for path in sorted(metadata_dir.glob("DOC-*.json")):
        metadata = _read_document_metadata(path)
        if metadata:
            documents.append(_redact_document(metadata))
    documents.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return documents


def get_deal_document(
    deal_id: str,
    document_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized = validate_document_id(document_id)
    metadata = _read_document_metadata(_metadata_path(package_dir, normalized))
    if not metadata:
        raise FileNotFoundError(normalized)
    return _redact_document(metadata)


def create_deal_document(
    *,
    deal_id: str,
    filename: str | None,
    content_type: str | None,
    stream: BinaryIO,
    document_type: str = "",
    source_note: str = "",
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    max_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    document_id = new_document_id()
    original_filename = _safe_original_filename(filename)
    stored_filename = f"{document_id}{_filename_extension(original_filename)}"
    storage_path = f"data_room/raw/{stored_filename}"
    target = _safe_relative_path(package_dir, storage_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha256()
    size_bytes = 0
    try:
        with target.open("wb") as handle:
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise ValueError(f"document exceeds max upload size: {max_bytes} bytes")
                digest.update(chunk)
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise

    now = deal_store.utc_now_iso()
    metadata = {
        "schema_version": DEAL_DOCUMENT_SCHEMA,
        "document_id": document_id,
        "deal_id": deal_store.validate_deal_id(deal_id),
        "filename": stored_filename,
        "original_filename": original_filename,
        "content_type": _clean_text(content_type, max_len=128) or "application/octet-stream",
        "size_bytes": size_bytes,
        "sha256": digest.hexdigest(),
        "document_type": _clean_text(document_type, max_len=80),
        "source_note": _clean_text(source_note, max_len=500),
        "storage_path": storage_path,
        "metadata_path": f"data_room/metadata/{document_id}.json",
        "status": "uploaded",
        "document_status": "active",
        "parse_status": "not_started",
        "analysis_source_status": "pending",
        "index_status": "not_requested",
        "parser_kind": "document",
        "parse_runs": [],
        "current_parse_run_id": None,
        "wiki_status": "pending",
        "wiki_path": None,
        "created_at": now,
        "updated_at": now,
        "created_by": created_by,
        "parse_task_id": None,
        "parsed_artifact_path": None,
    }
    _write_document_metadata(package_dir, metadata)
    _sync_manifest_documents(package_dir)
    primary_market_wiki.rebuild_primary_market_wiki(
        deal_id,
        wiki_root=wiki_root,
        append_audit=False,
    )
    deal_store.append_audit_event(
        deal_id,
        {
            "event_type": "deal_document_uploaded",
            "document_id": document_id,
            "filename": original_filename,
            "size_bytes": size_bytes,
            "sha256": digest.hexdigest(),
            "created_by": created_by,
        },
        wiki_root=wiki_root,
    )
    return _redact_document(metadata)


def delete_deal_document(
    deal_id: str,
    document_id: str,
    *,
    deleted_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized = validate_document_id(document_id)
    metadata_path = _metadata_path(package_dir, normalized)
    metadata = _read_document_metadata(metadata_path)
    if not metadata:
        raise FileNotFoundError(normalized)
    from services import primary_market_materials

    primary_market_materials.disable_analysis_sources_for_deleted_document(
        deal_id,
        normalized,
        disabled_by=deleted_by,
        wiki_root=wiki_root,
    )
    storage_path = str(metadata.get("storage_path") or "")
    if storage_path:
        _safe_relative_path(package_dir, storage_path).unlink(missing_ok=True)
    primary_market_wiki.remove_material_company_wiki(
        deal_id,
        normalized,
        wiki_root=wiki_root,
    )
    metadata_path.unlink(missing_ok=True)
    _sync_manifest_documents(package_dir)
    try:
        from services import deal_evidence

        deal_evidence.build_deal_evidence_package(
            deal_id,
            built_by=deleted_by,
            wiki_root=wiki_root,
        )
    except Exception as exc:
        (package_dir / "evidence" / "evidence_snapshot.json").unlink(missing_ok=True)
        deal_store.append_audit_event(
            deal_id,
            {
                "event_type": "deal_evidence_invalidated_after_document_delete",
                "document_id": normalized,
                "error_type": type(exc).__name__,
                "deleted_by": deleted_by,
            },
            wiki_root=wiki_root,
        )
    try:
        from services import deal_evidence_milvus

        cleanup_required = bool(
            deal_evidence_milvus.primary_market_milvus_index_enabled()
            or (package_dir / deal_evidence_milvus.MILVUS_INDEX_RECEIPT_PATH).is_file()
        )
        if cleanup_required:
            deal_evidence_milvus.remove_deal_document_rows(
                deal_id,
                normalized,
                deleted_by=deleted_by,
                wiki_root=wiki_root,
            )
    except Exception as exc:
        deal_store.append_audit_event(
            deal_id,
            {
                "event_type": "deal_document_milvus_cleanup_failed",
                "document_id": normalized,
                "error_type": type(exc).__name__,
                "deleted_by": deleted_by,
            },
            wiki_root=wiki_root,
        )
    primary_market_wiki.rebuild_primary_market_wiki(
        deal_id,
        wiki_root=wiki_root,
        append_audit=False,
    )
    deal_store.append_audit_event(
        deal_id,
        {
            "event_type": "deal_document_deleted",
            "document_id": normalized,
            "filename": metadata.get("original_filename"),
            "deleted_by": deleted_by,
        },
        wiki_root=wiki_root,
    )
    return {"ok": True, "document_id": normalized}


def bind_parser_task(
    deal_id: str,
    document_id: str,
    *,
    task_id: str,
    artifact_path: str | None = None,
    note: str = "",
    bound_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    normalized_document_id = validate_document_id(document_id)
    normalized_task_id = validate_parser_task_id(task_id)
    metadata_path = _metadata_path(package_dir, normalized_document_id)
    metadata = _read_document_metadata(metadata_path)
    if not metadata:
        raise FileNotFoundError(normalized_document_id)

    parsed_artifact_path = _safe_artifact_path(artifact_path)
    result_dir = DOCUMENT_PARSER_RESULTS_ROOT / normalized_task_id
    artifact_exists = result_dir.is_dir()
    if parsed_artifact_path:
        artifact_exists = (result_dir / parsed_artifact_path).exists()

    quoted_task_id = quote(normalized_task_id, safe="")
    now = deal_store.utc_now_iso()
    metadata.update({
        "status": "parse_bound",
        "parser_kind": metadata.get("parser_kind") or "document",
        "parse_status": "queued",
        "wiki_status": "pending",
        "parse_task_id": normalized_task_id,
        "parsed_artifact_path": parsed_artifact_path or None,
        "parser_status_url": f"/api/documents/status/{quoted_task_id}",
        "parser_result_url": f"/api/documents/result/{quoted_task_id}",
        "parser_page_url": f"/documents?task={quoted_task_id}",
        "parser_artifact_url": (
            f"/api/documents/artifact/{quoted_task_id}/{parsed_artifact_path}"
            if parsed_artifact_path
            else None
        ),
        "parser_artifact_exists": artifact_exists,
        "parse_bind_note": _clean_text(note, max_len=500),
        "parse_bound_at": now,
        "parse_bound_by": bound_by,
        "updated_at": now,
    })
    _write_document_metadata(package_dir, metadata)
    _sync_manifest_documents(package_dir)
    primary_market_wiki.rebuild_primary_market_wiki(
        deal_id,
        wiki_root=wiki_root,
        append_audit=False,
    )
    deal_store.append_audit_event(
        deal_id,
        {
            "event_type": "deal_document_parser_task_bound",
            "document_id": normalized_document_id,
            "task_id": normalized_task_id,
            "artifact_path": parsed_artifact_path or None,
            "artifact_exists": artifact_exists,
            "bound_by": bound_by,
        },
        wiki_root=wiki_root,
    )
    return _redact_document(metadata)
