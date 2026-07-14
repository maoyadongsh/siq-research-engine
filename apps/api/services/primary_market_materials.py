"""Contracts and safe paths for primary-market prospectus materials."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping

from services import deal_documents, deal_store, primary_market_prospectus_quality
from services.path_config import PDF_RESULTS_ROOT

DEAL_DOCUMENT_SCHEMA_V1 = "siq_deal_document_v1"
DEAL_DOCUMENT_SCHEMA_V2 = "siq_deal_document_v2"
PRIMARY_MARKET_PARSE_RUN_SCHEMA = "siq_primary_market_parse_run_v1"
PRIMARY_MARKET_ANALYSIS_SOURCE_SCHEMA = "siq_primary_market_analysis_source_v1"
DEAL_EVIDENCE_SNAPSHOT_SCHEMA = "siq_deal_evidence_snapshot_v1"
PRIMARY_MARKET_ANALYSIS_SOURCES_SCHEMA = "siq_primary_market_analysis_sources_v1"
PRIMARY_MARKET_UPLOAD_RESPONSE_SCHEMA = "siq_primary_market_prospectus_upload_v1"
PRIMARY_MARKET_ARCHIVE_MANIFEST_SCHEMA = "siq_primary_market_archive_manifest_v1"

PROSPECTUS_DOCUMENT_TYPE = "prospectus"
CN_A_SHARE_PROSPECTUS_PROFILE = "cn_a_share_prospectus"

DOCUMENT_ID_RE = re.compile(r"^DOC-[A-Z0-9]{12,32}$")
PARSE_RUN_ID_RE = re.compile(r"^PRUN-[0-9]{8}-[A-Z0-9]{12,32}$")
EVIDENCE_SNAPSHOT_HASH_RE = re.compile(r"^[a-f0-9]{64}$")

MARKETS = frozenset({"CN"})
EXCHANGES = frozenset({"SSE", "SZSE", "BSE"})
BOARDS = frozenset({"main", "star", "chinext", "beijing", "bse", "sme"})
FILING_STAGES = frozenset(
    {
        "application_draft",
        "pre_disclosure",
        "pre_disclosure_update",
        "inquiry_response_draft",
        "registration_draft",
        "registration_effective",
        "final_prospectus",
        "issuance",
        "listed",
        "withdrawn",
        "terminated",
    }
)
DOCUMENT_PROFILES = frozenset({CN_A_SHARE_PROSPECTUS_PROFILE})
PARSER_KINDS = frozenset({"pdf", "document"})

DOCUMENT_STATUSES = frozenset({"active", "superseded", "deleted"})
PARSE_STATUSES = frozenset(
    {
        "not_started",
        "submitting",
        "queued",
        "parsing",
        "archiving",
        "succeeded",
        "failed",
        "cancelled",
        "interrupted",
    }
)
ANALYSIS_SOURCE_STATUSES = frozenset(
    {
        "pending",
        "ready",
        "ready_with_restrictions",
        "review_required",
        "blocked",
        "disabled",
        "superseded",
    }
)
INDEX_STATUSES = frozenset({"not_requested", "queued", "indexing", "indexed", "failed"})

DOCUMENT_STATE_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "active": frozenset({"superseded", "deleted"}),
    "superseded": frozenset(),
    "deleted": frozenset(),
}
PARSE_STATE_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "not_started": frozenset({"submitting"}),
    "submitting": frozenset({"queued", "failed", "cancelled", "interrupted"}),
    "queued": frozenset({"parsing", "failed", "cancelled", "interrupted"}),
    "parsing": frozenset({"archiving", "failed", "cancelled", "interrupted"}),
    "archiving": frozenset({"succeeded", "failed", "cancelled", "interrupted"}),
    # A new immutable parse run may replace any terminal run.
    "succeeded": frozenset({"submitting"}),
    "failed": frozenset({"submitting"}),
    "cancelled": frozenset({"submitting"}),
    "interrupted": frozenset({"submitting"}),
}
ANALYSIS_SOURCE_STATE_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "pending": frozenset({"ready", "ready_with_restrictions", "review_required", "blocked"}),
    "review_required": frozenset({"ready", "ready_with_restrictions", "blocked"}),
    "ready": frozenset({"disabled", "superseded"}),
    "ready_with_restrictions": frozenset({"disabled", "superseded"}),
    "blocked": frozenset(),
    "disabled": frozenset(),
    "superseded": frozenset(),
}
INDEX_STATE_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "not_requested": frozenset({"queued"}),
    "queued": frozenset({"indexing", "failed"}),
    "indexing": frozenset({"indexed", "failed"}),
    "indexed": frozenset(),
    "failed": frozenset({"queued"}),
}

PARSER_SUCCESS_STATUSES = frozenset({"completed", "succeeded", "success", "done", "finished"})
PARSER_FAILURE_STATUSES = frozenset({"failed", "error", "failure"})
PARSER_CANCELLED_STATUSES = frozenset({"cancelled", "canceled"})
PARSER_QUEUED_STATUSES = frozenset({"uploaded", "queued", "pending"})
PARSER_PROCESSING_STATUSES = frozenset({"submitting", "submitted", "processing", "running"})
PARSER_ARTIFACT_ALLOWLIST = frozenset(
    {
        "artifact_manifest.json",
        "result_manifest.json",
        "metadata.json",
        "hash_manifest.json",
        "result.md",
        "result_complete.md",
        "content_list.json",
        "content_list_enhanced.json",
        "document_full.json",
        "financial_data.json",
        "financial_checks.json",
        "quality_report.json",
        "table_index.json",
    }
)
DEFAULT_MAX_PROSPECTUS_BYTES = int(
    os.environ.get("SIQ_PRIMARY_MARKET_PROSPECTUS_MAX_FILE_BYTES")
    or os.environ.get("SIQ_PRIMARY_MARKET_PROSPECTUS_MAX_BYTES")
    or os.environ.get("SIQ_PDF_UPLOAD_MAX_FILE_BYTES")
    or str(100 * 1024 * 1024)
)
CHUNK_SIZE = 1024 * 1024


class ArtifactPromotionError(RuntimeError):
    """Raised when a completed parser task cannot be promoted safely."""


_STATE_MACHINES: Mapping[str, Mapping[str, frozenset[str]]] = {
    "document": DOCUMENT_STATE_TRANSITIONS,
    "parse": PARSE_STATE_TRANSITIONS,
    "source": ANALYSIS_SOURCE_STATE_TRANSITIONS,
    "index": INDEX_STATE_TRANSITIONS,
}


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _enum_value(
    value: str,
    *,
    field: str,
    allowed: frozenset[str],
    transform: Callable[[str], str],
) -> str:
    normalized = transform(str(value or "").strip())
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{field} must be one of: {choices}")
    return normalized


def validate_document_id(document_id: str) -> str:
    normalized = str(document_id or "").strip().upper()
    if not DOCUMENT_ID_RE.fullmatch(normalized):
        raise ValueError("document_id must be DOC- followed by 12-32 uppercase letters or numbers")
    return normalized


def validate_parse_run_id(parse_run_id: str) -> str:
    normalized = str(parse_run_id or "").strip().upper()
    if not PARSE_RUN_ID_RE.fullmatch(normalized):
        raise ValueError("parse_run_id must be PRUN-YYYYMMDD- followed by 12-32 uppercase letters or numbers")
    return normalized


def validate_source_id(source_id: str) -> str:
    normalized = str(source_id or "").strip().upper()
    parts = normalized.split(":")
    if len(parts) != 4 or parts[0] != "PM":
        raise ValueError("source_id must be PM:{deal_id}:{document_id}:{parse_run_id}")
    _, deal_id, document_id, parse_run_id = parts
    deal_store.validate_deal_id(deal_id)
    validate_document_id(document_id)
    validate_parse_run_id(parse_run_id)
    return normalized


def primary_market_source_id(deal_id: str, document_id: str, parse_run_id: str) -> str:
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    normalized_document_id = validate_document_id(document_id)
    normalized_parse_run_id = validate_parse_run_id(parse_run_id)
    return validate_source_id(
        f"PM:{normalized_deal_id}:{normalized_document_id}:{normalized_parse_run_id}"
    )


def validate_evidence_snapshot_hash(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not EVIDENCE_SNAPSHOT_HASH_RE.fullmatch(normalized):
        raise ValueError("evidence_snapshot_hash must be a 64-character SHA256 hex digest")
    return normalized


def validate_market(value: str) -> str:
    return _enum_value(value, field="market", allowed=MARKETS, transform=str.upper)


def validate_exchange(value: str) -> str:
    return _enum_value(value, field="exchange", allowed=EXCHANGES, transform=str.upper)


def validate_board(value: str) -> str:
    return _enum_value(value, field="board", allowed=BOARDS, transform=str.lower)


def validate_filing_stage(value: str) -> str:
    return _enum_value(value, field="filing_stage", allowed=FILING_STAGES, transform=str.lower)


def validate_document_profile(value: str) -> str:
    return _enum_value(
        value,
        field="document_profile",
        allowed=DOCUMENT_PROFILES,
        transform=str.lower,
    )


def validate_parser_kind(value: str) -> str:
    return _enum_value(value, field="parser_kind", allowed=PARSER_KINDS, transform=str.lower)


def _validate_status(value: str, *, field: str, allowed: frozenset[str]) -> str:
    return _enum_value(value, field=field, allowed=allowed, transform=str.lower)


def validate_document_status(value: str) -> str:
    return _validate_status(value, field="document_status", allowed=DOCUMENT_STATUSES)


def validate_parse_status(value: str) -> str:
    return _validate_status(value, field="parse_status", allowed=PARSE_STATUSES)


def validate_analysis_source_status(value: str) -> str:
    return _validate_status(
        value,
        field="analysis_source_status",
        allowed=ANALYSIS_SOURCE_STATUSES,
    )


def validate_index_status(value: str) -> str:
    return _validate_status(value, field="index_status", allowed=INDEX_STATUSES)


def validate_state_transition(state_kind: str, current: str, target: str) -> str:
    kind = str(state_kind or "").strip().lower()
    machine = _STATE_MACHINES.get(kind)
    if machine is None:
        raise ValueError(f"unknown state kind: {state_kind}")
    normalized_current = str(current or "").strip().lower()
    normalized_target = str(target or "").strip().lower()
    if normalized_current not in machine:
        raise ValueError(f"invalid {kind} state: {current}")
    if normalized_target not in machine:
        raise ValueError(f"invalid {kind} state: {target}")
    if normalized_current == normalized_target:
        return normalized_target
    if normalized_target not in machine[normalized_current]:
        raise ValueError(f"illegal {kind} state transition: {normalized_current} -> {normalized_target}")
    return normalized_target


def validate_document_state_transition(current: str, target: str) -> str:
    return validate_state_transition("document", current, target)


def validate_parse_state_transition(current: str, target: str) -> str:
    return validate_state_transition("parse", current, target)


def validate_analysis_source_state_transition(current: str, target: str) -> str:
    return validate_state_transition("source", current, target)


def validate_index_state_transition(current: str, target: str) -> str:
    return validate_state_transition("index", current, target)


def new_parse_run_id(*, now: datetime | None = None) -> str:
    instant = now or datetime.now(timezone.utc)
    identifier = f"PRUN-{instant.astimezone(timezone.utc):%Y%m%d}-{uuid.uuid4().hex[:16].upper()}"
    return validate_parse_run_id(identifier)


def _safe_deal_child(
    deal_id: str,
    *relative_parts: str,
    wiki_root: Path | str | None = None,
) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root).resolve()
    relative = Path(*relative_parts)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("primary-market material path escapes deal package")
    target = (package_dir / relative).resolve()
    try:
        target.relative_to(package_dir)
    except ValueError as exc:
        raise ValueError("primary-market material path escapes deal package") from exc
    return target


def deal_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    return _safe_deal_child(deal_id, wiki_root=wiki_root)


def deal_raw_pdf_path(
    deal_id: str,
    document_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> Path:
    normalized = validate_document_id(document_id)
    return _safe_deal_child(
        deal_id,
        "data_room",
        "raw",
        f"{normalized}.pdf",
        wiki_root=wiki_root,
    )


def deal_document_metadata_path(
    deal_id: str,
    document_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> Path:
    normalized = validate_document_id(document_id)
    return _safe_deal_child(
        deal_id,
        "data_room",
        "metadata",
        f"{normalized}.json",
        wiki_root=wiki_root,
    )


def deal_document_parse_root(
    deal_id: str,
    document_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> Path:
    normalized = validate_document_id(document_id)
    return _safe_deal_child(deal_id, "parsed_documents", normalized, wiki_root=wiki_root)


def deal_parse_runs_dir(
    deal_id: str,
    document_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> Path:
    normalized = validate_document_id(document_id)
    return _safe_deal_child(
        deal_id,
        "parsed_documents",
        normalized,
        "runs",
        wiki_root=wiki_root,
    )


def deal_parse_run_dir(
    deal_id: str,
    document_id: str,
    parse_run_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> Path:
    normalized_document_id = validate_document_id(document_id)
    normalized_run_id = validate_parse_run_id(parse_run_id)
    return _safe_deal_child(
        deal_id,
        "parsed_documents",
        normalized_document_id,
        "runs",
        normalized_run_id,
        wiki_root=wiki_root,
    )


def deal_current_parse_run_path(
    deal_id: str,
    document_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> Path:
    normalized = validate_document_id(document_id)
    return _safe_deal_child(
        deal_id,
        "parsed_documents",
        normalized,
        "current.json",
        wiki_root=wiki_root,
    )


def deal_parse_run_archive_manifest_path(
    deal_id: str,
    document_id: str,
    parse_run_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> Path:
    normalized_document_id = validate_document_id(document_id)
    normalized_parse_run_id = validate_parse_run_id(parse_run_id)
    return _safe_deal_child(
        deal_id,
        "parsed_documents",
        normalized_document_id,
        "runs",
        normalized_parse_run_id,
        "archive_manifest.json",
        wiki_root=wiki_root,
    )


def deal_analysis_sources_path(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> Path:
    return _safe_deal_child(deal_id, "sources", "analysis_sources.json", wiki_root=wiki_root)


def deal_evidence_snapshot_path(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> Path:
    return _safe_deal_child(deal_id, "evidence", "evidence_snapshot.json", wiki_root=wiki_root)


def normalize_deal_document(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("deal document metadata must be an object")
    normalized = dict(payload)
    schema_version = str(normalized.get("schema_version") or DEAL_DOCUMENT_SCHEMA_V1).strip()
    if schema_version not in {DEAL_DOCUMENT_SCHEMA_V1, DEAL_DOCUMENT_SCHEMA_V2}:
        raise ValueError(f"unsupported DealDocument schema_version: {schema_version}")

    normalized["document_id"] = validate_document_id(str(normalized.get("document_id") or ""))
    normalized["deal_id"] = deal_store.validate_deal_id(str(normalized.get("deal_id") or ""))

    if schema_version == DEAL_DOCUMENT_SCHEMA_V1:
        legacy_status = str(normalized.get("status") or "uploaded").strip().lower()
        if legacy_status not in {"uploaded", "parse_bound"}:
            raise ValueError(f"unsupported v1 DealDocument status: {legacy_status}")
        normalized["legacy_schema_version"] = DEAL_DOCUMENT_SCHEMA_V1
        normalized["document_status"] = "active"
        if legacy_status == "parse_bound":
            normalized["parser_kind"] = "document"
            normalized["parse_status"] = (
                "succeeded" if normalized.get("parser_artifact_exists") is True else "queued"
            )
        else:
            normalized["parse_status"] = "not_started"
        normalized["analysis_source_status"] = "pending"
        normalized["index_status"] = "not_requested"
        normalized["current_parse_run_id"] = None
        normalized["schema_version"] = DEAL_DOCUMENT_SCHEMA_V2
        return normalized

    normalized["schema_version"] = DEAL_DOCUMENT_SCHEMA_V2
    normalized["document_status"] = validate_document_status(
        str(normalized.get("document_status") or "active")
    )
    normalized["parse_status"] = validate_parse_status(
        str(normalized.get("parse_status") or "not_started")
    )
    normalized["analysis_source_status"] = validate_analysis_source_status(
        str(normalized.get("analysis_source_status") or "pending")
    )
    normalized["index_status"] = validate_index_status(
        str(normalized.get("index_status") or "not_requested")
    )

    optional_validators: tuple[tuple[str, Callable[[str], str]], ...] = (
        ("market", validate_market),
        ("exchange", validate_exchange),
        ("board", validate_board),
        ("filing_stage", validate_filing_stage),
        ("document_profile", validate_document_profile),
        ("parser_kind", validate_parser_kind),
        ("current_parse_run_id", validate_parse_run_id),
        ("supersedes_document_id", validate_document_id),
    )
    for field, validator in optional_validators:
        value = normalized.get(field)
        if value not in {None, ""}:
            normalized[field] = validator(str(value))
        elif field in {"current_parse_run_id", "supersedes_document_id"}:
            normalized[field] = None

    if "parse_runs" in normalized and not isinstance(normalized["parse_runs"], list):
        raise ValueError("parse_runs must be an array")
    normalized.setdefault("parse_runs", [])
    return normalized


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_package_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    deal_store.ensure_deal_package_dirs(package_dir)
    (package_dir / "sources").mkdir(parents=True, exist_ok=True)
    return package_dir


def _read_metadata(
    deal_id: str,
    document_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    _require_package_dir(deal_id, wiki_root=wiki_root)
    path = deal_document_metadata_path(deal_id, document_id, wiki_root=wiki_root)
    payload = deal_store.read_json(path, None)
    if not isinstance(payload, dict):
        raise FileNotFoundError(document_id)
    return normalize_deal_document(payload)


def _write_metadata(
    metadata: dict[str, Any],
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    normalized = normalize_deal_document(metadata)
    normalized["updated_at"] = deal_store.utc_now_iso()
    path = deal_document_metadata_path(
        normalized["deal_id"],
        normalized["document_id"],
        wiki_root=wiki_root,
    )
    deal_store.write_json(path, normalized)
    _sync_manifest_documents(normalized["deal_id"], wiki_root=wiki_root)
    return normalized


def _sync_manifest_documents(deal_id: str, *, wiki_root: Path | str | None = None) -> None:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    documents = list_primary_market_materials(deal_id, wiki_root=wiki_root)
    manifest = deal_store.read_json(package_dir / "manifest.json", {}) or {}
    existing = manifest.get("documents") if isinstance(manifest.get("documents"), list) else []
    primary_ids = {str(item.get("document_id") or "") for item in documents}
    retained = [item for item in existing if str(item.get("document_id") or "") not in primary_ids]
    retained.extend(
        {
            key: item.get(key)
            for key in (
                "document_id",
                "original_filename",
                "storage_path",
                "metadata_path",
                "content_type",
                "size_bytes",
                "sha256",
                "document_type",
                "document_profile",
                "document_status",
                "parse_status",
                "analysis_source_status",
                "current_parse_run_id",
                "supersedes_document_id",
                "created_at",
            )
        }
        for item in documents
    )
    manifest["documents"] = retained
    manifest["updated_at"] = deal_store.utc_now_iso()
    deal_store.write_json(package_dir / "manifest.json", manifest)


def get_primary_market_material(
    deal_id: str,
    document_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    return _public_document(_read_metadata(deal_id, document_id, wiki_root=wiki_root))


def _public_document(metadata: dict[str, Any]) -> dict[str, Any]:
    payload = deal_store.redact_public_payload(metadata)
    if payload.get("document_type") == PROSPECTUS_DOCUMENT_TYPE:
        payload["original_url"] = (
            f"/api/primary-market/projects/{payload['deal_id']}/materials/"
            f"{payload['document_id']}/original"
        )
    return payload


def list_primary_market_materials(
    deal_id: str,
    *,
    wiki_root: Path | str | None = None,
    include_all: bool = False,
) -> list[dict[str, Any]]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    items: list[dict[str, Any]] = []
    for path in sorted((package_dir / "data_room" / "metadata").glob("DOC-*.json")):
        payload = deal_store.read_json(path, None)
        if not isinstance(payload, dict):
            continue
        try:
            normalized = normalize_deal_document(payload)
        except ValueError:
            continue
        if include_all or normalized.get("document_type") == PROSPECTUS_DOCUMENT_TYPE:
            items.append(_public_document(normalized))
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return items


def _optional_date(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError("document_date must use YYYY-MM-DD") from exc


def _safe_filename(filename: str | None) -> str:
    name = re.split(r"[\\/]+", str(filename or "prospectus.pdf"))[-1].strip()
    return re.sub(r"[\x00-\x1f\x7f]", "", name)[:180] or "prospectus.pdf"


def create_prospectus_document(
    *,
    deal_id: str,
    filename: str | None,
    content_type: str | None,
    stream: BinaryIO,
    exchange: str | None = None,
    board: str | None = None,
    filing_stage: str | None = None,
    document_date: str | None = None,
    issuer_name: str | None = None,
    source_note: str | None = None,
    supersedes_document_id: str | None = None,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    max_bytes: int = DEFAULT_MAX_PROSPECTUS_BYTES,
) -> dict[str, Any]:
    package_dir = _require_package_dir(deal_id, wiki_root=wiki_root)
    original_filename = _safe_filename(filename)
    if Path(original_filename).suffix.lower() != ".pdf":
        raise ValueError("invalid_pdf: prospectus filename must end in .pdf")
    mime = str(content_type or "").split(";", 1)[0].strip().lower()
    if mime not in {"", "application/pdf", "application/octet-stream"}:
        raise ValueError("invalid_pdf: prospectus content type must be application/pdf")

    normalized_exchange = validate_exchange(exchange) if str(exchange or "").strip() else None
    normalized_board = validate_board(board) if str(board or "").strip() else None
    normalized_stage = validate_filing_stage(filing_stage) if str(filing_stage or "").strip() else None
    normalized_date = _optional_date(document_date)
    supersedes = validate_document_id(supersedes_document_id) if str(supersedes_document_id or "").strip() else None
    if supersedes:
        prior = _read_metadata(deal_id, supersedes, wiki_root=wiki_root)
        if prior.get("document_status") != "active":
            raise ValueError("supersedes_document_id must reference an active material")

    raw_dir = package_dir / "data_room" / "raw"
    digest = hashlib.sha256()
    size_bytes = 0
    header = b""
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=raw_dir, prefix=".prospectus-", suffix=".tmp", delete=False) as handle:
            temp_path = Path(handle.name)
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                if not isinstance(chunk, bytes):
                    raise ValueError("invalid_pdf: upload stream must contain bytes")
                if not header:
                    header = chunk[:8]
                size_bytes += len(chunk)
                if size_bytes > max_bytes:
                    raise ValueError(f"prospectus_too_large: maximum is {max_bytes} bytes")
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if size_bytes == 0 or not header.startswith(b"%PDF-"):
            raise ValueError("invalid_pdf: empty file or missing PDF header")

        sha256 = digest.hexdigest()
        for existing in list_primary_market_materials(deal_id, wiki_root=wiki_root):
            if existing.get("sha256") == sha256 and existing.get("document_status") == "active":
                temp_path.unlink(missing_ok=True)
                return {"document": existing, "reused": True}

        document_id = deal_documents.new_document_id()
        target = deal_raw_pdf_path(deal_id, document_id, wiki_root=wiki_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, target)
        temp_path = None
        now = deal_store.utc_now_iso()
        metadata = {
            "schema_version": DEAL_DOCUMENT_SCHEMA_V2,
            "document_id": document_id,
            "deal_id": deal_store.validate_deal_id(deal_id),
            "document_type": PROSPECTUS_DOCUMENT_TYPE,
            "document_profile": CN_A_SHARE_PROSPECTUS_PROFILE,
            "parser_kind": "pdf",
            "market": "CN",
            "exchange": normalized_exchange,
            "board": normalized_board,
            "filing_stage": normalized_stage,
            "document_date": normalized_date,
            "issuer_name": str(issuer_name or "").strip()[:255] or None,
            "original_filename": original_filename,
            "filename": f"{document_id}.pdf",
            "content_type": "application/pdf",
            "size_bytes": size_bytes,
            "sha256": sha256,
            "source_note": str(source_note or "").strip()[:500],
            "storage_path": f"data_room/raw/{document_id}.pdf",
            "metadata_path": f"data_room/metadata/{document_id}.json",
            "document_status": "active",
            "parse_status": "not_started",
            "analysis_source_status": "pending",
            "index_status": "not_requested",
            "current_parse_run_id": None,
            "supersedes_document_id": supersedes,
            "parse_runs": [],
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
        }
        written = _write_metadata(metadata, wiki_root=wiki_root)
        deal_store.append_audit_event(
            deal_id,
            {
                "event_type": "deal_prospectus_uploaded",
                "document_id": document_id,
                "sha256": sha256,
                "size_bytes": size_bytes,
                "created_by": created_by,
            },
            wiki_root=wiki_root,
        )
        return {"document": _public_document(written), "reused": False}
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _replace_run_summary(metadata: dict[str, Any], run: dict[str, Any]) -> None:
    runs = [item for item in metadata.get("parse_runs") or [] if isinstance(item, dict)]
    runs = [item for item in runs if item.get("parse_run_id") != run.get("parse_run_id")]
    runs.append(run)
    metadata["parse_runs"] = runs


def create_parse_run(
    deal_id: str,
    document_id: str,
    *,
    submitted_by: dict[str, Any] | None = None,
    parse_config_hash: str | None = None,
    parser_version: str | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    metadata = _read_metadata(deal_id, document_id, wiki_root=wiki_root)
    if metadata.get("document_status") != "active":
        raise ValueError("material_state_conflict: only active material can be parsed")
    parse_run_id = new_parse_run_id()
    now = deal_store.utc_now_iso()
    run = {
        "schema_version": PRIMARY_MARKET_PARSE_RUN_SCHEMA,
        "parse_run_id": parse_run_id,
        "deal_id": metadata["deal_id"],
        "document_id": metadata["document_id"],
        "parser_kind": "pdf",
        "parser_task_id": None,
        "market": "CN",
        "document_profile": CN_A_SHARE_PROSPECTUS_PROFILE,
        "raw_sha256": metadata.get("sha256"),
        "parse_config_hash": parse_config_hash,
        "parser_version": parser_version,
        "status": "submitting",
        "artifact_root": None,
        "quality_status": "pending",
        "capabilities": {},
        "submitted_by": submitted_by,
        "created_at": now,
        "updated_at": now,
    }
    _replace_run_summary(metadata, run)
    metadata["parse_status"] = "submitting"
    _write_metadata(metadata, wiki_root=wiki_root)
    return run


def update_parse_run_submission(
    deal_id: str,
    document_id: str,
    parse_run_id: str,
    *,
    parser_task_id: str | None = None,
    status: str = "queued",
    parse_config_hash: str | None = None,
    parser_version: str | None = None,
    failure_code: str | None = None,
    failure_message: str | None = None,
    actor: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    normalized_run_id = validate_parse_run_id(parse_run_id)
    normalized_status = validate_parse_status(status)
    metadata = _read_metadata(deal_id, document_id, wiki_root=wiki_root)
    runs = [item for item in metadata.get("parse_runs") or [] if isinstance(item, dict)]
    run = next((item for item in runs if item.get("parse_run_id") == normalized_run_id), None)
    if run is None:
        raise FileNotFoundError(normalized_run_id)
    if parser_task_id:
        run["parser_task_id"] = deal_documents.validate_parser_task_id(parser_task_id)
    run["status"] = normalized_status
    run["parse_config_hash"] = parse_config_hash or run.get("parse_config_hash")
    run["parser_version"] = parser_version or run.get("parser_version")
    run["failure_code"] = str(failure_code or "")[:80] or None
    run["failure_message"] = str(failure_message or "")[:500] or None
    run["updated_at"] = deal_store.utc_now_iso()
    metadata["parse_status"] = normalized_status
    _replace_run_summary(metadata, run)
    _write_metadata(metadata, wiki_root=wiki_root)
    event_type = "deal_prospectus_parse_submit_failed" if normalized_status == "failed" else "deal_prospectus_parse_submitted"
    deal_store.append_audit_event(
        deal_id,
        {
            "event_type": event_type,
            "document_id": document_id,
            "parse_run_id": normalized_run_id,
            "parser_task_id": run.get("parser_task_id"),
            "status": normalized_status,
            "failure_code": run.get("failure_code"),
            "actor": actor,
        },
        wiki_root=wiki_root,
    )
    return dict(run)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parser_result_dir(parser_task_id: str, *, results_root: Path | str | None = None) -> Path:
    task_id = deal_documents.validate_parser_task_id(parser_task_id)
    root = Path(results_root) if results_root is not None else PDF_RESULTS_ROOT
    root = root.resolve()
    candidate = (root / task_id).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("parser result path escapes results root") from exc
    if not candidate.is_dir():
        raise FileNotFoundError(task_id)
    return candidate


def _parser_result_manifest(result_dir: Path, parser_task_id: str) -> tuple[dict[str, Any], str]:
    for name in ("result_manifest.json", "artifact_manifest.json"):
        payload = deal_store.read_json(result_dir / name, None)
        if not isinstance(payload, dict):
            continue
        manifest_task_id = str(payload.get("task_id") or "")
        if manifest_task_id and manifest_task_id != parser_task_id:
            raise ValueError("parser result manifest task identity mismatch")
        return payload, name
    raise ValueError("parser result manifest is missing or invalid")


def _expected_artifact(manifest: dict[str, Any], name: str) -> dict[str, Any]:
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    entry = artifacts.get(name)
    return entry if isinstance(entry, dict) else {}


def _copy_verified_artifact(
    source: Path,
    target: Path,
    *,
    expected: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not source.is_file():
        raise FileNotFoundError(source.name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    with target.open("rb") as handle:
        os.fsync(handle.fileno())
    size_bytes = target.stat().st_size
    sha256 = _sha256_file(target)
    expected = expected or {}
    if expected.get("size_bytes") is not None and int(expected["size_bytes"]) != size_bytes:
        raise ValueError(f"parser artifact size mismatch: {source.name}")
    if expected.get("sha256") and str(expected["sha256"]).lower() != sha256:
        raise ValueError(f"parser artifact hash mismatch: {source.name}")
    return {"path": target.name, "size_bytes": size_bytes, "sha256": sha256}


def _bundle_hash(files: list[dict[str, Any]]) -> str:
    lines = [f"{item['path']}:{item['sha256']}" for item in sorted(files, key=lambda item: item["path"])]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _verify_existing_archive(run_dir: Path, manifest: dict[str, Any]) -> None:
    files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    if not files:
        raise ValueError("existing parse run archive manifest has no files")
    verified: list[dict[str, Any]] = []
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("existing parse run archive manifest contains an invalid file entry")
        name = str(entry.get("path") or "")
        if Path(name).name != name or not name:
            raise ValueError("existing parse run archive contains an unsafe file path")
        path = run_dir / name
        if not path.is_file():
            raise ValueError(f"existing parse run archive is missing {name}")
        actual = {"path": name, "size_bytes": path.stat().st_size, "sha256": _sha256_file(path)}
        if entry.get("size_bytes") != actual["size_bytes"] or entry.get("sha256") != actual["sha256"]:
            raise ValueError(f"existing parse run archive hash conflict: {name}")
        verified.append(actual)
    if manifest.get("bundle_sha256") != _bundle_hash(verified):
        raise ValueError("existing parse run archive bundle hash conflict")


def _read_sources_registry(deal_id: str, *, wiki_root: Path | str | None = None) -> dict[str, Any]:
    _require_package_dir(deal_id, wiki_root=wiki_root)
    path = deal_analysis_sources_path(deal_id, wiki_root=wiki_root)
    payload = deal_store.read_json(path, None)
    if not isinstance(payload, dict):
        payload = {}
    sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
    return {
        "schema_version": PRIMARY_MARKET_ANALYSIS_SOURCES_SCHEMA,
        "deal_id": deal_store.validate_deal_id(deal_id),
        "sources": [item for item in sources if isinstance(item, dict)],
        "updated_at": payload.get("updated_at"),
    }


def list_analysis_sources(deal_id: str, *, wiki_root: Path | str | None = None) -> list[dict[str, Any]]:
    return deal_store.redact_public_payload(
        _read_sources_registry(deal_id, wiki_root=wiki_root)["sources"]
    )


def _upsert_analysis_source(
    deal_id: str,
    source: dict[str, Any],
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    registry = _read_sources_registry(deal_id, wiki_root=wiki_root)
    sources = []
    for item in registry["sources"]:
        if str(item.get("source_id") or "") == str(source.get("source_id") or ""):
            continue
        if (
            source.get("status") in {"ready", "ready_with_restrictions"}
            and item.get("document_id") == source.get("document_id")
            and item.get("status") in {"ready", "ready_with_restrictions"}
        ):
            item = {
                **item,
                "status": "superseded",
                "superseded_by_source_id": source.get("source_id"),
                "updated_at": deal_store.utc_now_iso(),
            }
        sources.append(item)
    sources.append(source)
    registry["sources"] = sources
    registry["updated_at"] = deal_store.utc_now_iso()
    deal_store.write_json(deal_analysis_sources_path(deal_id, wiki_root=wiki_root), registry)
    return source


def get_analysis_source_for_document(
    deal_id: str,
    document_id: str,
    *,
    parse_run_id: str | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any] | None:
    normalized_document_id = validate_document_id(document_id)
    sources = _read_sources_registry(deal_id, wiki_root=wiki_root)["sources"]
    matches = [
        item for item in sources
        if item.get("document_id") == normalized_document_id
        and (not parse_run_id or item.get("parse_run_id") == parse_run_id)
    ]
    matches.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return deal_store.redact_public_payload(matches[0]) if matches else None


def _source_from_archive(
    metadata: dict[str, Any],
    run: dict[str, Any],
    archive_manifest: dict[str, Any],
    quality: dict[str, Any],
    *,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    deal_id = metadata["deal_id"]
    document_id = metadata["document_id"]
    parse_run_id = run["parse_run_id"]
    status = validate_analysis_source_status(str(quality.get("status") or "blocked"))
    if status == "ready" and not _env_truthy(
        "SIQ_PRIMARY_MARKET_AUTO_ACTIVATE_QUALITY_PASS", default=True
    ):
        status = "review_required"
    now = deal_store.utc_now_iso()
    source = {
        "schema_version": PRIMARY_MARKET_ANALYSIS_SOURCE_SCHEMA,
        "source_id": primary_market_source_id(deal_id, document_id, parse_run_id),
        "domain": "primary_market",
        "source_type": "primary_market_prospectus",
        "deal_id": deal_id,
        "market": "CN",
        "company_id": f"PRIMARY:{deal_id}",
        "filing_id": f"PROSPECTUS:{document_id}",
        "document_id": document_id,
        "parse_run_id": parse_run_id,
        "artifact_manifest_path": (
            f"parsed_documents/{document_id}/runs/{parse_run_id}/archive_manifest.json"
        ),
        "archive_manifest_sha256": archive_manifest.get("manifest_sha256"),
        "status": status,
        "capabilities": quality.get("capabilities") or {},
        "quality_status": quality.get("status"),
        "activated_by": actor if status in {"ready", "ready_with_restrictions"} else None,
        "activated_at": now if status in {"ready", "ready_with_restrictions"} else None,
        "created_at": now,
        "updated_at": now,
    }
    return source


def promote_parse_run_artifacts(
    deal_id: str,
    document_id: str,
    parse_run_id: str,
    *,
    parser_task_id: str | None = None,
    promoted_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    results_root: Path | str | None = None,
) -> dict[str, Any]:
    normalized_run_id = validate_parse_run_id(parse_run_id)
    metadata = _read_metadata(deal_id, document_id, wiki_root=wiki_root)
    runs = [item for item in metadata.get("parse_runs") or [] if isinstance(item, dict)]
    run = next((item for item in runs if item.get("parse_run_id") == normalized_run_id), None)
    if run is None:
        raise FileNotFoundError(normalized_run_id)
    task_id = deal_documents.validate_parser_task_id(parser_task_id or str(run.get("parser_task_id") or ""))
    result_dir = _parser_result_dir(task_id, results_root=results_root)
    manifest, manifest_name = _parser_result_manifest(result_dir, task_id)
    runs_dir = deal_parse_runs_dir(deal_id, document_id, wiki_root=wiki_root)
    runs_dir.mkdir(parents=True, exist_ok=True)
    target_dir = deal_parse_run_dir(deal_id, document_id, normalized_run_id, wiki_root=wiki_root)
    lock_target = runs_dir / f".{normalized_run_id}.promotion"

    with deal_store._locked_path(lock_target):
        if target_dir.is_dir():
            existing = deal_store.read_json(target_dir / "archive_manifest.json", None)
            if not isinstance(existing, dict):
                raise ValueError("existing parse run archive has no valid manifest")
            _verify_existing_archive(target_dir, existing)
            return {
                "status": "existing",
                "parse_run": dict(run),
                "archive_manifest": existing,
                "analysis_source": get_analysis_source_for_document(
                    deal_id, document_id, parse_run_id=normalized_run_id, wiki_root=wiki_root
                ),
            }

        staging = runs_dir / f".staging-{normalized_run_id}-{uuid.uuid4().hex[:8]}"
        staging.mkdir(parents=False, exist_ok=False)
        copied: list[dict[str, Any]] = []
        try:
            canonical_name = "result_complete.md" if (result_dir / "result_complete.md").is_file() else "result.md"
            if not (result_dir / canonical_name).is_file():
                raise ValueError("canonical parser Markdown is missing")

            for name in sorted(PARSER_ARTIFACT_ALLOWLIST - {"artifact_manifest.json", "result_manifest.json"}):
                source_path = result_dir / name
                if not source_path.is_file():
                    continue
                target_name = name
                if name == "quality_report.json":
                    target_name = "parser_quality_report.json"
                copied.append(
                    _copy_verified_artifact(
                        source_path,
                        staging / target_name,
                        expected=_expected_artifact(manifest, name),
                    )
                )
            copied.append(
                _copy_verified_artifact(
                    result_dir / canonical_name,
                    staging / "document.md",
                    expected=_expected_artifact(manifest, canonical_name),
                )
            )
            deal_store.write_json(staging / "result_manifest.json", manifest)
            if not any(item["path"] == "result_manifest.json" for item in copied):
                copied.append({
                    "path": "result_manifest.json",
                    "size_bytes": (staging / "result_manifest.json").stat().st_size,
                    "sha256": _sha256_file(staging / "result_manifest.json"),
                })

            quality = primary_market_prospectus_quality.write_prospectus_quality_report(
                staging,
                overwrite=True,
            )
            quality_entry = {
                "path": "quality_report.json",
                "size_bytes": (staging / "quality_report.json").stat().st_size,
                "sha256": _sha256_file(staging / "quality_report.json"),
            }
            copied = [item for item in copied if item["path"] != "quality_report.json"]
            copied.append(quality_entry)
            bundle_sha256 = _bundle_hash(copied)
            archive_manifest = {
                "schema_version": PRIMARY_MARKET_ARCHIVE_MANIFEST_SCHEMA,
                "deal_id": metadata["deal_id"],
                "document_id": metadata["document_id"],
                "parse_run_id": normalized_run_id,
                "parser_task_id": task_id,
                "parser_manifest_name": manifest_name,
                "raw_sha256": metadata.get("sha256"),
                "parse_config_hash": run.get("parse_config_hash"),
                "files": sorted(copied, key=lambda item: item["path"]),
                "bundle_sha256": bundle_sha256,
                "created_at": deal_store.utc_now_iso(),
            }
            deal_store.write_json(staging / "archive_manifest.json", archive_manifest)
            archive_manifest["manifest_sha256"] = _sha256_file(staging / "archive_manifest.json")
            directory_fd = os.open(staging, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            os.rename(staging, target_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        run.update({
            "status": "succeeded",
            "artifact_root": f"parsed_documents/{document_id}/runs/{normalized_run_id}",
            "quality_status": quality.get("status"),
            "capabilities": quality.get("capabilities") or {},
            "updated_at": deal_store.utc_now_iso(),
        })
        _replace_run_summary(metadata, run)
        metadata["parse_status"] = "succeeded"
        metadata["analysis_source_status"] = quality.get("status")
        metadata["current_parse_run_id"] = normalized_run_id
        deal_store.write_json(
            deal_current_parse_run_path(deal_id, document_id, wiki_root=wiki_root),
            {
                "schema_version": PRIMARY_MARKET_PARSE_RUN_SCHEMA,
                "deal_id": deal_id,
                "document_id": document_id,
                "parse_run_id": normalized_run_id,
                "archive_manifest_path": (
                    f"parsed_documents/{document_id}/runs/{normalized_run_id}/archive_manifest.json"
                ),
                "updated_at": deal_store.utc_now_iso(),
            },
        )
        _write_metadata(metadata, wiki_root=wiki_root)
        source = _source_from_archive(metadata, run, archive_manifest, quality, actor=promoted_by)
        _upsert_analysis_source(deal_id, source, wiki_root=wiki_root)

    if (
        source["status"] in {"ready", "ready_with_restrictions"}
        and metadata.get("supersedes_document_id")
    ):
        supersede_material(
            deal_id,
            str(metadata["supersedes_document_id"]),
            superseding_document_id=document_id,
            note="Automatically superseded after replacement source activation",
            superseded_by=promoted_by or {},
            wiki_root=wiki_root,
        )

    deal_store.append_audit_event(
        deal_id,
        {
            "event_type": "deal_prospectus_artifacts_promoted",
            "document_id": document_id,
            "parse_run_id": normalized_run_id,
            "parser_task_id": task_id,
            "bundle_sha256": archive_manifest.get("bundle_sha256"),
            "quality_status": quality.get("status"),
            "promoted_by": promoted_by,
        },
        wiki_root=wiki_root,
    )
    if source["status"] in {"ready", "ready_with_restrictions"}:
        deal_store.append_audit_event(
            deal_id,
            {
                "event_type": "deal_prospectus_source_activated",
                "source_id": source["source_id"],
                "document_id": document_id,
                "parse_run_id": normalized_run_id,
                "activated_by": promoted_by,
            },
            wiki_root=wiki_root,
        )
    evidence_result: dict[str, Any] | None = None
    evidence_warning: str | None = None
    if source["status"] in {"ready", "ready_with_restrictions"}:
        try:
            from services import deal_evidence

            evidence_result = deal_evidence.build_deal_evidence_package(
                deal_id,
                built_by=promoted_by,
                wiki_root=wiki_root,
            )
        except Exception as exc:
            evidence_warning = f"evidence_build_failed:{type(exc).__name__}"
            deal_store.append_audit_event(
                deal_id,
                {
                    "event_type": "deal_prospectus_evidence_build_failed",
                    "document_id": document_id,
                    "parse_run_id": normalized_run_id,
                    "error_type": type(exc).__name__,
                    "promoted_by": promoted_by,
                },
                wiki_root=wiki_root,
            )
    snapshot = (
        evidence_result.get("evidence_snapshot")
        if isinstance(evidence_result, dict)
        else _refresh_snapshot(deal_id, actor=promoted_by, wiki_root=wiki_root)
    )
    return {
        "status": "promoted",
        "parse_run": dict(run),
        "archive_manifest": archive_manifest,
        "quality": quality,
        "analysis_source": deal_store.redact_public_payload(source),
        "evidence": evidence_result,
        "evidence_snapshot": snapshot,
        "warning": evidence_warning,
    }


def _latest_parse_run(metadata: dict[str, Any]) -> dict[str, Any] | None:
    runs = [dict(item) for item in metadata.get("parse_runs") or [] if isinstance(item, dict)]
    runs.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return runs[0] if runs else None


def read_material_parse_status(
    deal_id: str,
    document_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    metadata = _read_metadata(deal_id, document_id, wiki_root=wiki_root)
    latest = _latest_parse_run(metadata)
    current_run_id = str(metadata.get("current_parse_run_id") or "")
    current_run = next(
        (
            dict(item) for item in metadata.get("parse_runs") or []
            if isinstance(item, dict) and item.get("parse_run_id") == current_run_id
        ),
        None,
    )
    return {
        "deal_id": metadata["deal_id"],
        "document_id": metadata["document_id"],
        "document": deal_store.redact_public_payload(metadata),
        "parse_run": deal_store.redact_public_payload(latest),
        "current_parse_run": deal_store.redact_public_payload(current_run),
        "analysis_source": get_analysis_source_for_document(
            deal_id,
            document_id,
            parse_run_id=metadata.get("current_parse_run_id"),
            wiki_root=wiki_root,
        ),
    }


def read_material_detail(
    deal_id: str,
    document_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    status = read_material_parse_status(deal_id, document_id, wiki_root=wiki_root)
    metadata = _read_metadata(deal_id, document_id, wiki_root=wiki_root)
    latest_run = status.get("parse_run") if isinstance(status.get("parse_run"), dict) else None
    quality = None
    if latest_run and latest_run.get("parse_run_id"):
        quality = deal_store.read_json(
            deal_parse_run_dir(
                deal_id,
                document_id,
                str(latest_run["parse_run_id"]),
                wiki_root=wiki_root,
            ) / "quality_report.json",
            None,
        )
    materials = list_primary_market_materials(deal_id, wiki_root=wiki_root)
    version_chain = [
        {
            "document_id": item.get("document_id"),
            "supersedes_document_id": item.get("supersedes_document_id"),
            "superseded_by_document_id": item.get("superseded_by_document_id"),
            "document_status": item.get("document_status"),
            "document_date": item.get("document_date"),
        }
        for item in materials
        if item.get("document_id") == metadata.get("document_id")
        or item.get("document_id") == metadata.get("supersedes_document_id")
        or item.get("supersedes_document_id") == metadata.get("document_id")
        or item.get("superseded_by_document_id") == metadata.get("document_id")
    ]
    snapshot = deal_store.read_json(
        deal_evidence_snapshot_path(deal_id, wiki_root=wiki_root), {},
    ) or {}
    current_hash = str(snapshot.get("snapshot_hash") or "")
    receipts_payload = deal_store.read_json(
        deal_package_dir(deal_id, wiki_root=wiki_root) / "phases" / "startup_receipts.json", {},
    ) or {}
    receipts = receipts_payload.get("agents") if isinstance(receipts_payload.get("agents"), dict) else {}
    stale_receipts = sum(
        1
        for receipt in receipts.values()
        if isinstance(receipt, dict)
        and current_hash
        and receipt.get("evidence_snapshot_hash") != current_hash
    )
    stale_reports = 0
    package_dir = deal_package_dir(deal_id, wiki_root=wiki_root)
    for relative in ("phases/r1_reports.json", "phases/r2_reports.json", "phases/r3_reports.json", "phases/r4_decision.json"):
        payload = deal_store.read_json(package_dir / relative, None)
        if not isinstance(payload, dict) or not current_hash:
            continue
        candidates = payload.values() if relative.endswith(("r1_reports.json", "r2_reports.json")) else [payload]
        stale_reports += sum(
            1
            for report in candidates
            if isinstance(report, dict)
            and report.get("evidence_snapshot_hash")
            and report.get("evidence_snapshot_hash") != current_hash
        )
    return {
        **status,
        "quality": quality if isinstance(quality, dict) else None,
        "quality_report": quality if isinstance(quality, dict) else None,
        "capabilities": (
            quality.get("capabilities") if isinstance(quality, dict) else {}
        ),
        "version_chain": version_chain,
        "stale_receipt_count": stale_receipts,
        "stale_report_count": stale_reports,
        "evidence_snapshot_hash": current_hash or None,
    }


def reconcile_parse_run(
    deal_id: str,
    document_id: str,
    *,
    parser_task: dict[str, Any] | None,
    reconciled_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    results_root: Path | str | None = None,
) -> dict[str, Any]:
    metadata = _read_metadata(deal_id, document_id, wiki_root=wiki_root)
    run = _latest_parse_run(metadata)
    if not run:
        return {**read_material_parse_status(deal_id, document_id, wiki_root=wiki_root), "reconciled": False}
    task_id = str(run.get("parser_task_id") or "")
    if not task_id:
        return {**read_material_parse_status(deal_id, document_id, wiki_root=wiki_root), "reconciled": False}
    if not isinstance(parser_task, dict):
        updated = update_parse_run_submission(
            deal_id,
            document_id,
            run["parse_run_id"],
            parser_task_id=task_id,
            status="interrupted",
            failure_code="parser_task_missing",
            failure_message="Parser task is missing during recovery",
            actor=reconciled_by,
            wiki_root=wiki_root,
        )
        return {**read_material_parse_status(deal_id, document_id, wiki_root=wiki_root), "parse_run": updated, "reconciled": True}

    parser_status = str(parser_task.get("status") or parser_task.get("stage") or "").strip().lower()
    if parser_status in PARSER_SUCCESS_STATUSES:
        if run.get("status") == "succeeded":
            return {**read_material_parse_status(deal_id, document_id, wiki_root=wiki_root), "reconciled": False}
        update_parse_run_submission(
            deal_id,
            document_id,
            run["parse_run_id"],
            parser_task_id=task_id,
            status="archiving",
            actor=reconciled_by,
            wiki_root=wiki_root,
        )
        try:
            promoted = promote_parse_run_artifacts(
                deal_id,
                document_id,
                run["parse_run_id"],
                parser_task_id=task_id,
                promoted_by=reconciled_by,
                wiki_root=wiki_root,
                results_root=results_root,
            )
        except Exception as exc:
            update_parse_run_submission(
                deal_id,
                document_id,
                run["parse_run_id"],
                parser_task_id=task_id,
                status="failed",
                failure_code="artifact_promotion_failed",
                failure_message=str(exc),
                actor=reconciled_by,
                wiki_root=wiki_root,
            )
            deal_store.append_audit_event(
                deal_id,
                {
                    "event_type": "deal_prospectus_artifact_conflict" if "conflict" in str(exc).lower() else "deal_prospectus_archive_failed",
                    "document_id": document_id,
                    "parse_run_id": run["parse_run_id"],
                    "parser_task_id": task_id,
                    "error_type": type(exc).__name__,
                    "reconciled_by": reconciled_by,
                },
                wiki_root=wiki_root,
            )
            raise ArtifactPromotionError(str(exc)) from exc
        return {**read_material_parse_status(deal_id, document_id, wiki_root=wiki_root), "promotion": promoted, "reconciled": True}
    if parser_status in PARSER_FAILURE_STATUSES:
        status = "failed"
    elif parser_status in PARSER_CANCELLED_STATUSES:
        status = "cancelled"
    elif parser_status in PARSER_QUEUED_STATUSES:
        status = "queued"
    elif parser_status in PARSER_PROCESSING_STATUSES:
        status = "parsing"
    else:
        return {
            **read_material_parse_status(deal_id, document_id, wiki_root=wiki_root),
            "reconciled": False,
            "warning": "unknown_parser_status",
        }
    changed = status != run.get("status")
    if changed:
        update_parse_run_submission(
            deal_id,
            document_id,
            run["parse_run_id"],
            parser_task_id=task_id,
            status=status,
            failure_code=str(parser_task.get("error_code") or "") if status == "failed" else None,
            failure_message=str(parser_task.get("error") or parser_task.get("message") or "") if status == "failed" else None,
            actor=reconciled_by,
            wiki_root=wiki_root,
        )
        deal_store.append_audit_event(
            deal_id,
            {
                "event_type": "deal_prospectus_parse_status_changed",
                "document_id": document_id,
                "parse_run_id": run["parse_run_id"],
                "previous_status": run.get("status"),
                "status": status,
                "reconciled_by": reconciled_by,
            },
            wiki_root=wiki_root,
        )
    return {**read_material_parse_status(deal_id, document_id, wiki_root=wiki_root), "reconciled": changed}


def _refresh_snapshot(deal_id: str, *, actor: dict[str, Any] | None, wiki_root: Path | str | None) -> dict[str, Any]:
    from services import deal_evidence

    return deal_evidence.refresh_evidence_snapshot(deal_id, built_by=actor, wiki_root=wiki_root)


def review_analysis_source(
    deal_id: str,
    document_id: str,
    *,
    decision: str,
    capability_overrides: dict[str, str] | None = None,
    note: str,
    reviewer: dict[str, Any],
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    metadata = _read_metadata(deal_id, document_id, wiki_root=wiki_root)
    source = get_analysis_source_for_document(deal_id, document_id, wiki_root=wiki_root)
    if not source:
        raise FileNotFoundError(document_id)
    normalized_decision = str(decision or "").strip().lower()
    if normalized_decision not in {"activate", "block"}:
        raise ValueError("quality_review_invalid: decision must be activate or block")
    note = str(note or "").strip()
    if not note:
        raise ValueError("quality_review_invalid: review note is required")
    run_dir = deal_parse_run_dir(
        deal_id,
        document_id,
        str(source.get("parse_run_id") or ""),
        wiki_root=wiki_root,
    )
    quality = deal_store.read_json(run_dir / "quality_report.json", {}) or {}
    original_capabilities = source.get("capabilities") if isinstance(source.get("capabilities"), dict) else {}
    capabilities = dict(original_capabilities)
    allowed_capabilities = {"text_evidence", "source_page_trace", "financial_facts", "semantic_index"}
    for key, value in (capability_overrides or {}).items():
        if key not in allowed_capabilities or value not in {"ready", "blocked", "pending"}:
            raise ValueError("quality_review_invalid: invalid capability override")
        capabilities[key] = value
    if normalized_decision == "activate":
        if quality.get("capabilities", {}).get("text_evidence") != "ready":
            raise ValueError("quality_review_invalid: missing canonical Markdown cannot be overridden")
        if capabilities.get("source_page_trace") != "ready":
            raise ValueError("quality_review_invalid: source page trace is required for activation")
        status = "ready" if capabilities.get("financial_facts") == "ready" else "ready_with_restrictions"
    else:
        status = "blocked"
    now = deal_store.utc_now_iso()
    source.update({
        "status": status,
        "capabilities": capabilities,
        "review": {
            "decision": normalized_decision,
            "reviewer": reviewer,
            "note": note[:1000],
            "reviewed_at": now,
            "original_status": source.get("status"),
            "original_capabilities": original_capabilities,
        },
        "activated_by": reviewer if normalized_decision == "activate" else None,
        "activated_at": now if normalized_decision == "activate" else None,
        "updated_at": now,
    })
    _upsert_analysis_source(deal_id, source, wiki_root=wiki_root)
    metadata["analysis_source_status"] = status
    if normalized_decision == "activate":
        metadata["current_parse_run_id"] = source["parse_run_id"]
        deal_store.write_json(
            deal_current_parse_run_path(deal_id, document_id, wiki_root=wiki_root),
            {
                "schema_version": PRIMARY_MARKET_PARSE_RUN_SCHEMA,
                "deal_id": deal_id,
                "document_id": document_id,
                "parse_run_id": source["parse_run_id"],
                "updated_at": now,
            },
        )
    _write_metadata(metadata, wiki_root=wiki_root)
    snapshot = _refresh_snapshot(deal_id, actor=reviewer, wiki_root=wiki_root)
    return {
        "document": get_primary_market_material(deal_id, document_id, wiki_root=wiki_root),
        "analysis_source": deal_store.redact_public_payload(source),
        "evidence_snapshot": snapshot,
    }


def disable_analysis_source(
    deal_id: str,
    document_id: str,
    *,
    note: str,
    disabled_by: dict[str, Any],
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    metadata = _read_metadata(deal_id, document_id, wiki_root=wiki_root)
    source = get_analysis_source_for_document(deal_id, document_id, wiki_root=wiki_root)
    if not source:
        raise FileNotFoundError(document_id)
    if source.get("status") not in {"ready", "ready_with_restrictions", "review_required"}:
        raise ValueError("material_state_conflict: source cannot be disabled from current state")
    source.update({
        "status": "disabled",
        "disabled_by": disabled_by,
        "disabled_at": deal_store.utc_now_iso(),
        "disable_note": str(note or "").strip()[:1000],
        "updated_at": deal_store.utc_now_iso(),
    })
    _upsert_analysis_source(deal_id, source, wiki_root=wiki_root)
    metadata["analysis_source_status"] = "disabled"
    _write_metadata(metadata, wiki_root=wiki_root)
    deal_store.append_audit_event(
        deal_id,
        {
            "event_type": "deal_prospectus_source_disabled",
            "source_id": source.get("source_id"),
            "document_id": document_id,
            "note": source.get("disable_note"),
            "disabled_by": disabled_by,
        },
        wiki_root=wiki_root,
    )
    snapshot = _refresh_snapshot(deal_id, actor=disabled_by, wiki_root=wiki_root)
    return {
        "document": get_primary_market_material(deal_id, document_id, wiki_root=wiki_root),
        "analysis_source": deal_store.redact_public_payload(source),
        "evidence_snapshot": snapshot,
    }


def supersede_material(
    deal_id: str,
    document_id: str,
    *,
    superseding_document_id: str,
    note: str = "",
    superseded_by: dict[str, Any],
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    old = _read_metadata(deal_id, document_id, wiki_root=wiki_root)
    replacement = _read_metadata(deal_id, superseding_document_id, wiki_root=wiki_root)
    if old["document_id"] == replacement["document_id"]:
        raise ValueError("material_state_conflict: material cannot supersede itself")
    if old.get("document_status") != "active" or replacement.get("document_status") != "active":
        raise ValueError("material_state_conflict: both materials must be active")
    if replacement.get("analysis_source_status") not in {"ready", "ready_with_restrictions"}:
        raise ValueError("material_state_conflict: replacement source is not ready")
    old["document_status"] = "superseded"
    old["analysis_source_status"] = "superseded"
    old["superseded_by_document_id"] = replacement["document_id"]
    old["superseded_at"] = deal_store.utc_now_iso()
    old["supersede_note"] = str(note or "").strip()[:1000]
    replacement["supersedes_document_id"] = old["document_id"]
    _write_metadata(old, wiki_root=wiki_root)
    _write_metadata(replacement, wiki_root=wiki_root)
    source = get_analysis_source_for_document(deal_id, document_id, wiki_root=wiki_root)
    if source and source.get("status") in {"ready", "ready_with_restrictions", "disabled"}:
        source.update({
            "status": "superseded",
            "superseded_by_source_id": (
                get_analysis_source_for_document(deal_id, replacement["document_id"], wiki_root=wiki_root) or {}
            ).get("source_id"),
            "updated_at": deal_store.utc_now_iso(),
        })
        _upsert_analysis_source(deal_id, source, wiki_root=wiki_root)
    deal_store.append_audit_event(
        deal_id,
        {
            "event_type": "deal_prospectus_superseded",
            "document_id": old["document_id"],
            "superseding_document_id": replacement["document_id"],
            "note": old.get("supersede_note"),
            "superseded_by": superseded_by,
        },
        wiki_root=wiki_root,
    )
    snapshot = _refresh_snapshot(deal_id, actor=superseded_by, wiki_root=wiki_root)
    return {
        "document": get_primary_market_material(deal_id, document_id, wiki_root=wiki_root),
        "superseding_document": get_primary_market_material(
            deal_id, replacement["document_id"], wiki_root=wiki_root
        ),
        "analysis_source": source,
        "evidence_snapshot": snapshot,
    }


def material_artifact_path(
    deal_id: str,
    document_id: str,
    artifact_name: str,
    *,
    wiki_root: Path | str | None = None,
) -> Path:
    safe_name = str(artifact_name or "").strip()
    allowed = {
        "archive_manifest.json",
        "result_manifest.json",
        "document.md",
        "content_list.json",
        "content_list_enhanced.json",
        "document_full.json",
        "financial_data.json",
        "financial_checks.json",
        "quality_report.json",
        "parser_quality_report.json",
        "table_index.json",
    }
    if safe_name not in allowed or Path(safe_name).name != safe_name:
        raise ValueError("artifact is not available")
    metadata = _read_metadata(deal_id, document_id, wiki_root=wiki_root)
    parse_run_id = validate_parse_run_id(str(metadata.get("current_parse_run_id") or ""))
    path = deal_parse_run_dir(
        deal_id, document_id, parse_run_id, wiki_root=wiki_root
    ) / safe_name
    if not path.is_file():
        raise FileNotFoundError(safe_name)
    return path


def material_original_path(
    deal_id: str,
    document_id: str,
    *,
    wiki_root: Path | str | None = None,
) -> Path:
    metadata = _read_metadata(deal_id, document_id, wiki_root=wiki_root)
    if metadata.get("document_type") != PROSPECTUS_DOCUMENT_TYPE:
        raise ValueError("material is not a prospectus")
    path = deal_raw_pdf_path(deal_id, document_id, wiki_root=wiki_root)
    if not path.is_file():
        raise FileNotFoundError(document_id)
    return path


def material_source_page(
    deal_id: str,
    document_id: str,
    page_number: int,
    *,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    if page_number < 1:
        raise ValueError("page_number must be positive")
    sources = _read_sources_registry(deal_id, wiki_root=wiki_root)["sources"]
    active = next(
        (
            source for source in reversed(sources)
            if source.get("document_id") == validate_document_id(document_id)
            and source.get("status") in {"ready", "ready_with_restrictions"}
        ),
        None,
    )
    if not active:
        raise FileNotFoundError(document_id)
    path = deal_parse_run_dir(
        deal_id,
        document_id,
        str(active.get("parse_run_id") or ""),
        wiki_root=wiki_root,
    ) / "content_list_enhanced.json"
    if not path.is_file():
        fallback = path.with_name("content_list.json")
        if not fallback.is_file():
            raise FileNotFoundError("content_list_enhanced.json")
        path = fallback
    payload = deal_store.read_json(path, None)
    blocks: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("blocks"), list):
            candidates = [item for item in payload["blocks"] if isinstance(item, dict)]
        elif isinstance(payload.get("pages"), list):
            for page in payload["pages"]:
                if not isinstance(page, dict):
                    continue
                raw_page = page.get("page") or page.get("page_number") or page.get("page_idx")
                try:
                    normalized_page = int(raw_page) + (1 if "page_idx" in page else 0)
                except (TypeError, ValueError):
                    continue
                if normalized_page == page_number:
                    blocks.extend(item for item in page.get("blocks") or [] if isinstance(item, dict))
    elif isinstance(payload, list):
        candidates = [item for item in payload if isinstance(item, dict)]
    for block in candidates:
        raw_page = block.get("page") or block.get("page_number")
        zero_based = False
        if raw_page is None:
            raw_page = block.get("page_idx")
            zero_based = True
        try:
            normalized_page = int(raw_page) + (1 if zero_based else 0)
        except (TypeError, ValueError):
            continue
        if normalized_page == page_number:
            blocks.append(block)
    return {
        "deal_id": deal_store.validate_deal_id(deal_id),
        "document_id": validate_document_id(document_id),
        "page_number": page_number,
        "blocks": deal_store.redact_public_payload(blocks),
    }


def recover_primary_market_materials_on_startup(
    *,
    wiki_root: Path | str | None = None,
    results_root: Path | str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Recover locally decidable non-terminal runs without requiring a browser."""

    requested_limit = limit
    if requested_limit is None:
        requested_limit = int(os.environ.get("SIQ_PRIMARY_MARKET_RECONCILE_STARTUP_LIMIT", "50"))
    scan_limit = max(1, min(int(requested_limit), 500))
    root = deal_store.deals_root(wiki_root=wiki_root)
    result_root = Path(results_root) if results_root is not None else PDF_RESULTS_ROOT
    summary: dict[str, Any] = {
        "scanned": 0,
        "promoted": 0,
        "interrupted": 0,
        "pending": 0,
        "failed": 0,
        "errors": [],
    }
    if not root.is_dir():
        return summary
    for package_dir in sorted(root.iterdir()):
        if summary["scanned"] >= scan_limit:
            break
        if not package_dir.is_dir() or not (package_dir / "manifest.json").is_file():
            continue
        deal_id = package_dir.name
        metadata_dir = package_dir / "data_room" / "metadata"
        for path in sorted(metadata_dir.glob("DOC-*.json")):
            if summary["scanned"] >= scan_limit:
                break
            payload = deal_store.read_json(path, None)
            if not isinstance(payload, dict) or payload.get("document_type") != PROSPECTUS_DOCUMENT_TYPE:
                continue
            try:
                metadata = normalize_deal_document(payload)
                run = _latest_parse_run(metadata)
                if not run or run.get("status") in {"succeeded", "failed", "cancelled", "interrupted"}:
                    continue
                summary["scanned"] += 1
                task_id = str(run.get("parser_task_id") or "")
                if not task_id:
                    update_parse_run_submission(
                        deal_id,
                        metadata["document_id"],
                        run["parse_run_id"],
                        status="interrupted",
                        failure_code="submission_interrupted",
                        failure_message="API restarted before parser task identity was persisted",
                        wiki_root=wiki_root,
                    )
                    summary["interrupted"] += 1
                    continue
                parser_dir = result_root / task_id
                manifest_exists = any(
                    (parser_dir / name).is_file()
                    for name in ("result_manifest.json", "artifact_manifest.json")
                )
                markdown_exists = any(
                    (parser_dir / name).is_file()
                    for name in ("result_complete.md", "result.md")
                )
                if manifest_exists and markdown_exists:
                    recovered = reconcile_parse_run(
                        deal_id,
                        metadata["document_id"],
                        parser_task={"task_id": task_id, "status": "completed"},
                        reconciled_by={"username": "startup-reconciler"},
                        wiki_root=wiki_root,
                        results_root=result_root,
                    )
                    summary["promoted"] += int(bool(recovered.get("reconciled")))
                else:
                    summary["pending"] += 1
            except Exception as exc:
                summary["failed"] += 1
                summary["errors"].append({
                    "deal_id": deal_id,
                    "document_id": payload.get("document_id"),
                    "error_type": type(exc).__name__,
                })
    return summary


__all__ = [
    "ArtifactPromotionError",
    "ANALYSIS_SOURCE_STATUSES",
    "ANALYSIS_SOURCE_STATE_TRANSITIONS",
    "BOARDS",
    "CN_A_SHARE_PROSPECTUS_PROFILE",
    "DEAL_DOCUMENT_SCHEMA_V1",
    "DEAL_DOCUMENT_SCHEMA_V2",
    "DEAL_EVIDENCE_SNAPSHOT_SCHEMA",
    "DOCUMENT_PROFILES",
    "DOCUMENT_STATUSES",
    "DOCUMENT_STATE_TRANSITIONS",
    "EXCHANGES",
    "FILING_STAGES",
    "INDEX_STATUSES",
    "INDEX_STATE_TRANSITIONS",
    "MARKETS",
    "PARSER_KINDS",
    "PARSE_STATUSES",
    "PARSE_STATE_TRANSITIONS",
    "PRIMARY_MARKET_ANALYSIS_SOURCE_SCHEMA",
    "PRIMARY_MARKET_ANALYSIS_SOURCES_SCHEMA",
    "PRIMARY_MARKET_ARCHIVE_MANIFEST_SCHEMA",
    "PRIMARY_MARKET_PARSE_RUN_SCHEMA",
    "PRIMARY_MARKET_UPLOAD_RESPONSE_SCHEMA",
    "PROSPECTUS_DOCUMENT_TYPE",
    "deal_analysis_sources_path",
    "deal_current_parse_run_path",
    "deal_document_metadata_path",
    "deal_document_parse_root",
    "deal_evidence_snapshot_path",
    "deal_package_dir",
    "deal_parse_run_archive_manifest_path",
    "deal_parse_run_dir",
    "deal_parse_runs_dir",
    "deal_raw_pdf_path",
    "create_parse_run",
    "create_prospectus_document",
    "disable_analysis_source",
    "get_analysis_source_for_document",
    "get_primary_market_material",
    "list_analysis_sources",
    "list_primary_market_materials",
    "material_artifact_path",
    "material_original_path",
    "material_source_page",
    "new_parse_run_id",
    "normalize_deal_document",
    "promote_parse_run_artifacts",
    "primary_market_source_id",
    "read_material_detail",
    "read_material_parse_status",
    "recover_primary_market_materials_on_startup",
    "reconcile_parse_run",
    "review_analysis_source",
    "supersede_material",
    "update_parse_run_submission",
    "validate_analysis_source_state_transition",
    "validate_analysis_source_status",
    "validate_board",
    "validate_document_id",
    "validate_document_profile",
    "validate_document_state_transition",
    "validate_document_status",
    "validate_evidence_snapshot_hash",
    "validate_exchange",
    "validate_filing_stage",
    "validate_index_state_transition",
    "validate_index_status",
    "validate_market",
    "validate_parse_run_id",
    "validate_parse_state_transition",
    "validate_parse_status",
    "validate_parser_kind",
    "validate_source_id",
    "validate_state_transition",
]
