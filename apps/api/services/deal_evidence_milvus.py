"""Idempotent Deal Evidence indexing for the primary-market shared Milvus collection."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx

from services import deal_store, vector_retrieval

MILVUS_INDEX_RECEIPT_SCHEMA = "siq_deal_evidence_milvus_index_receipt_v1"
MILVUS_EVIDENCE_METADATA_SCHEMA = "siq_primary_market_evidence_vector_v1"
MILVUS_INDEX_RECEIPT_PATH = "evidence/milvus_index_receipt.json"
PRIMARY_MARKET_DOMAIN = "primary_market"
PROJECT_EVIDENCE_SOURCE_CLASS = "project_evidence"
DEFAULT_EMBEDDING_MODEL = vector_retrieval.DEFAULT_EMBEDDING_MODEL
DEFAULT_BATCH_SIZE = 32
MAX_INDEX_ITEMS = 10000
MAX_EXISTING_ROWS = 16384
_SNAPSHOT_HASH_RE = re.compile(r"^[a-fA-F0-9]{64}$")
_DOCUMENT_ID_RE = re.compile(r"^DOC-[A-Z0-9]{12,32}$")


class DealEvidenceMilvusIndexError(RuntimeError):
    def __init__(self, message: str, *, receipt: dict[str, Any] | None = None):
        super().__init__(message)
        self.receipt = receipt


def primary_market_milvus_index_enabled() -> bool:
    return str(os.getenv("SIQ_PRIMARY_MARKET_MILVUS_INDEX_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _require_package_dir(deal_id: str, *, wiki_root: Path | str | None = None) -> Path:
    package_dir = deal_store.safe_deal_dir(deal_id, wiki_root=wiki_root)
    if not (package_dir / "manifest.json").is_file():
        raise FileNotFoundError(deal_id)
    return package_dir


def _read_evidence_items(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ValueError("evidence_items_missing") from exc
    items: list[dict[str, Any]] = []
    invalid_lines = 0
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            invalid_lines += 1
            continue
        if isinstance(payload, dict):
            items.append(payload)
        else:
            invalid_lines += 1
    if invalid_lines:
        raise ValueError(f"evidence_items_invalid_lines:{invalid_lines}")
    if len(items) > MAX_INDEX_ITEMS:
        raise ValueError(f"evidence_items_limit_exceeded:{MAX_INDEX_ITEMS}")
    return items


def _read_snapshot(package_dir: Path, deal_id: str) -> dict[str, Any]:
    snapshot = deal_store.read_json(package_dir / "evidence" / "evidence_snapshot.json", None)
    if not isinstance(snapshot, dict):
        raise ValueError("evidence_snapshot_missing")
    if str(snapshot.get("deal_id") or "") != deal_id:
        raise ValueError("evidence_snapshot_deal_mismatch")
    snapshot_hash = str(snapshot.get("snapshot_hash") or "")
    if not _SNAPSHOT_HASH_RE.fullmatch(snapshot_hash):
        raise ValueError("evidence_snapshot_hash_invalid")
    return snapshot


def _assert_snapshot_current(package_dir: Path, deal_id: str, expected_hash: str) -> None:
    current = _read_snapshot(package_dir, deal_id)
    if str(current.get("snapshot_hash") or "") != expected_hash:
        raise ValueError("evidence_snapshot_changed_during_index")


def _build_records(
    items: list[dict[str, Any]],
    *,
    deal_id: str,
    snapshot_hash: str,
) -> tuple[list[dict[str, Any]], str]:
    records: list[dict[str, Any]] = []
    seen_evidence_ids: set[str] = set()
    for item in sorted(items, key=lambda value: str(value.get("evidence_id") or "")):
        evidence_id = str(item.get("evidence_id") or "").strip()
        item_deal_id = str(item.get("deal_id") or "").strip()
        document_id = str(item.get("document_id") or "").strip()
        source_id = str(item.get("source_id") or document_id).strip()
        wiki_path = str(item.get("wiki_path") or item.get("source_path") or "").strip()
        wiki_sha256 = str(item.get("wiki_sha256") or "").strip()
        parse_task_id = str(item.get("parse_task_id") or "").strip()
        parse_run_id = str(item.get("parse_run_id") or "").strip()
        original_path = str(item.get("original_path") or "").strip()
        original_sha256 = str(item.get("original_sha256") or "").strip()
        parser_source_path = str(item.get("parser_source_path") or "").strip()
        parser_source_sha256 = str(item.get("parser_source_sha256") or "").strip()
        text = str(item.get("quote") or item.get("claim") or "").strip()
        if not evidence_id:
            raise ValueError("evidence_id_missing")
        if evidence_id in seen_evidence_ids:
            raise ValueError(f"evidence_id_duplicate:{evidence_id}")
        if item_deal_id != deal_id:
            raise ValueError(f"evidence_item_deal_mismatch:{evidence_id}")
        if not document_id:
            raise ValueError(f"evidence_document_id_missing:{evidence_id}")
        if not source_id:
            raise ValueError(f"evidence_source_id_missing:{evidence_id}")
        if not wiki_path.startswith("wiki/company/materials/") or "data/wiki/companies" in wiki_path.lower():
            raise ValueError(f"evidence_wiki_path_invalid:{evidence_id}")
        if not _SNAPSHOT_HASH_RE.fullmatch(wiki_sha256):
            raise ValueError(f"evidence_wiki_sha256_invalid:{evidence_id}")
        if not parse_task_id and not parse_run_id:
            raise ValueError(f"evidence_parse_identity_missing:{evidence_id}")
        if not original_path.startswith("data_room/raw/") or ".." in Path(original_path).parts:
            raise ValueError(f"evidence_original_path_invalid:{evidence_id}")
        if not _SNAPSHOT_HASH_RE.fullmatch(original_sha256):
            raise ValueError(f"evidence_original_sha256_invalid:{evidence_id}")
        if not (
            parser_source_path.startswith("parsed_documents/")
            or parser_source_path.startswith("parser_results/")
        ) or ".." in Path(parser_source_path).parts:
            raise ValueError(f"evidence_parser_source_path_invalid:{evidence_id}")
        if not _SNAPSHOT_HASH_RE.fullmatch(parser_source_sha256):
            raise ValueError(f"evidence_parser_source_sha256_invalid:{evidence_id}")
        if not text:
            raise ValueError(f"evidence_text_missing:{evidence_id}")
        seen_evidence_ids.add(evidence_id)
        metadata = {
            "schema_version": MILVUS_EVIDENCE_METADATA_SCHEMA,
            "domain": PRIMARY_MARKET_DOMAIN,
            "source_class": PROJECT_EVIDENCE_SOURCE_CLASS,
            "project_fact": True,
            "project_tag": deal_id,
            "deal_id": deal_id,
            "evidence_id": evidence_id,
            "document_id": document_id,
            "source_id": source_id,
            "wiki_path": wiki_path,
            "wiki_sha256": wiki_sha256,
            "parse_task_id": parse_task_id or None,
            "parse_run_id": parse_run_id or None,
            "original_path": original_path,
            "original_sha256": original_sha256,
            "parser_source_path": parser_source_path,
            "parser_source_sha256": parser_source_sha256,
            "wiki_source_path": wiki_path,
            "wiki_source_sha256": wiki_sha256,
            "snapshot_hash": snapshot_hash,
            "text": text,
            "citation": str(item.get("citation") or ""),
        }
        metadata.update(
            {
                key: item.get(key)
                for key in (
                    "locator",
                    "dimension",
                    "evidence_type",
                    "source_path",
                    "parse_task_id",
                    "parse_run_id",
                    "source_url",
                    "artifact_url",
                )
                if item.get(key) is not None
            }
        )
        record_digest = _stable_hash(metadata)
        metadata["record_digest"] = record_digest
        records.append(
            {
                "project_tag": deal_id,
                "evidence_id": evidence_id,
                "text": text,
                "record_digest": record_digest,
                "metadata": metadata,
            }
        )
    index_digest = _stable_hash(
        {
            "schema_version": MILVUS_EVIDENCE_METADATA_SCHEMA,
            "deal_id": deal_id,
            "snapshot_hash": snapshot_hash,
            "record_digests": [record["record_digest"] for record in records],
        }
    )
    return records, index_digest


def _bind_embedding_contract(
    records: list[dict[str, Any]],
    *,
    content_index_digest: str,
    embedding_model: str,
    vector_field: str,
    vector_dimensions: int,
) -> str:
    index_digest = _stable_hash(
        {
            "content_index_digest": content_index_digest,
            "embedding_model": embedding_model,
            "vector_field": vector_field,
            "vector_dimensions": vector_dimensions,
        }
    )
    for record in records:
        record["metadata"].update(
            {
                "index_digest": index_digest,
                "embedding_model": embedding_model,
                "vector_field": vector_field,
                "vector_dimensions": vector_dimensions,
            }
        )
    return index_digest


def _embedding_model() -> str:
    return str(
        os.getenv("SIQ_EMBEDDING_MODEL")
        or os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_MODEL")
        or os.getenv("EMBEDDING_MODEL")
        or DEFAULT_EMBEDDING_MODEL
    ).strip()


def _embedding_api_key() -> str:
    return str(
        os.getenv("SIQ_EMBEDDING_API_KEY")
        or os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_API_KEY")
        or os.getenv("EMBEDDING_API_KEY")
        or ""
    ).strip()


def _embedding_batch_size() -> int:
    try:
        value = int(os.getenv("SIQ_PRIMARY_MARKET_EMBEDDING_BATCH_SIZE") or DEFAULT_BATCH_SIZE)
    except ValueError:
        value = DEFAULT_BATCH_SIZE
    return max(1, min(value, 256))


def _embed_texts(
    texts: list[str],
    *,
    dimensions: int,
    timeout: float,
) -> list[list[float]]:
    endpoint = vector_retrieval._embedding_endpoint()
    if not endpoint:
        raise ValueError("embedding_endpoint_not_configured")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = _embedding_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    vectors: list[list[float]] = []
    batch_size = _embedding_batch_size()
    with httpx.Client() as client:
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            response = client.post(
                endpoint,
                headers=headers,
                json={"model": _embedding_model(), "input": batch},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or len(data) != len(batch):
                raise ValueError("embedding_response_size_mismatch")
            ordered: dict[int, list[float]] = {}
            for fallback_index, item in enumerate(data):
                embedding = item.get("embedding") if isinstance(item, dict) else None
                if not isinstance(embedding, list):
                    raise ValueError("embedding_response_invalid")
                vector = [float(value) for value in embedding]
                if len(vector) != dimensions:
                    raise ValueError(
                        f"embedding_dimension_mismatch:expected={dimensions}:actual={len(vector)}"
                    )
                raw_index = item.get("index") if isinstance(item, dict) else None
                index = raw_index if isinstance(raw_index, int) else fallback_index
                ordered[index] = vector
            if set(ordered) != set(range(len(batch))):
                raise ValueError("embedding_response_index_invalid")
            vectors.extend(ordered[index] for index in range(len(batch)))
    return vectors


def _dtype_name(field: Any) -> str:
    dtype = getattr(field, "dtype", None)
    return str(getattr(dtype, "name", "") or dtype or "").upper()


def _collection_contract(collection: Any) -> dict[str, Any]:
    schema = getattr(collection, "schema", None)
    fields = list(getattr(schema, "fields", None) or [])
    fields_by_name = {
        str(getattr(field, "name", "") or "").strip(): field
        for field in fields
        if str(getattr(field, "name", "") or "").strip()
    }
    configured_vector_field = str(os.getenv("SIQ_MILVUS_VECTOR_FIELD") or "").strip()
    vector_candidates = [configured_vector_field, "embedding", "vector"]
    vector_candidates.extend(
        name for name, field in fields_by_name.items() if "VECTOR" in _dtype_name(field)
    )
    vector_field = next(
        (
            name
            for name in vector_candidates
            if name and name in fields_by_name and "VECTOR" in _dtype_name(fields_by_name[name])
        ),
        "",
    )
    if not vector_field:
        raise ValueError("milvus_vector_field_not_found")
    vector_params = getattr(fields_by_name[vector_field], "params", None)
    vector_params = vector_params if isinstance(vector_params, dict) else {}
    dimensions = int(vector_params.get("dim") or 0)
    if dimensions <= 0:
        raise ValueError("milvus_vector_dimensions_invalid")
    if "project_tag" not in fields_by_name or "metadata" not in fields_by_name:
        raise ValueError("milvus_evidence_fields_missing")
    primary_fields = [field for field in fields if bool(getattr(field, "is_primary", False))]
    if len(primary_fields) != 1 or not bool(getattr(primary_fields[0], "auto_id", False)):
        raise ValueError("milvus_auto_primary_key_required")
    primary_field = str(getattr(primary_fields[0], "name", "") or "")
    insert_fields = [
        str(getattr(field, "name", "") or "")
        for field in fields
        if not bool(getattr(field, "auto_id", False))
    ]
    if set(insert_fields) != {vector_field, "project_tag", "metadata"}:
        raise ValueError("milvus_insert_schema_unsupported")
    return {
        "vector_field": vector_field,
        "dimensions": dimensions,
        "primary_field": primary_field,
        "insert_fields": insert_fields,
    }


def _physical_collection() -> str:
    return vector_retrieval._physical_collection(vector_retrieval.SHARED_DEAL_COLLECTION)


def _open_collection() -> Any:
    from pymilvus import Collection, connections, utility  # type: ignore[import-not-found]

    alias = os.getenv("SIQ_PRIMARY_MARKET_MILVUS_ALIAS") or "siq_primary_market_evidence_index"
    connection_kwargs: dict[str, Any] = {
        "alias": alias,
        "host": os.getenv("SIQ_MILVUS_HOST") or os.getenv("MILVUS_HOST") or "127.0.0.1",
        "port": os.getenv("SIQ_MILVUS_PORT") or os.getenv("MILVUS_PORT") or "19530",
    }
    db_name = os.getenv("SIQ_MILVUS_DB") or os.getenv("MILVUS_DB")
    if db_name:
        connection_kwargs["db_name"] = db_name
    connections.connect(**connection_kwargs)
    physical_collection = _physical_collection()
    if not utility.has_collection(physical_collection, using=alias):
        raise ValueError(f"milvus_collection_missing:{physical_collection}")
    return Collection(physical_collection, using=alias)


def _query_existing_rows(
    collection: Any,
    *,
    primary_field: str,
    deal_id: str,
) -> list[dict[str, Any]]:
    collection.load()
    rows = collection.query(
        expr=f'project_tag == "{deal_id}"',
        output_fields=[primary_field, "metadata"],
        limit=MAX_EXISTING_ROWS,
    )
    return [item for item in rows if isinstance(item, dict)] if isinstance(rows, list) else []


def _existing_rows_match(
    rows: list[dict[str, Any]],
    *,
    records: list[dict[str, Any]],
    deal_id: str,
    snapshot_hash: str,
    index_digest: str,
) -> bool:
    if len(rows) != len(records):
        return False
    expected_evidence_ids = {str(record["evidence_id"]) for record in records}
    expected_record_digests = {
        str(record["evidence_id"]): str(record["record_digest"])
        for record in records
    }
    actual_evidence_ids: set[str] = set()
    for row in rows:
        metadata = _metadata(row.get("metadata"))
        if (
            metadata.get("domain") != PRIMARY_MARKET_DOMAIN
            or metadata.get("source_class") != PROJECT_EVIDENCE_SOURCE_CLASS
            or metadata.get("project_fact") is not True
            or metadata.get("deal_id") != deal_id
            or metadata.get("snapshot_hash") != snapshot_hash
            or metadata.get("index_digest") != index_digest
        ):
            return False
        evidence_id = str(metadata.get("evidence_id") or "")
        if not evidence_id or evidence_id in actual_evidence_ids:
            return False
        if str(metadata.get("record_digest") or "") != expected_record_digests.get(evidence_id):
            return False
        actual_evidence_ids.add(evidence_id)
    return actual_evidence_ids == expected_evidence_ids


def _insert_records(
    collection: Any,
    *,
    contract: dict[str, Any],
    records: list[dict[str, Any]],
    vectors: list[list[float]],
) -> None:
    columns = {
        contract["vector_field"]: vectors,
        "project_tag": [record["project_tag"] for record in records],
        "metadata": [record["metadata"] for record in records],
    }
    collection.insert([columns[field] for field in contract["insert_fields"]])
    collection.flush()


def _delete_rows(collection: Any, *, primary_field: str, rows: list[dict[str, Any]]) -> int:
    ids: list[int] = []
    for row in rows:
        value = row.get(primary_field)
        if not isinstance(value, int):
            raise ValueError("milvus_existing_primary_key_invalid")
        ids.append(value)
    if not ids:
        return 0
    collection.delete(expr=f"{primary_field} in [" + ",".join(str(value) for value in ids) + "]")
    collection.flush()
    return len(ids)


def _sync_manifest(package_dir: Path, receipt: dict[str, Any]) -> None:
    manifest_path = package_dir / "manifest.json"
    manifest = deal_store.read_json(manifest_path, {}) or {}
    evidence = manifest.get("evidence") if isinstance(manifest.get("evidence"), dict) else {}
    evidence["milvus_index_receipt_path"] = MILVUS_INDEX_RECEIPT_PATH
    evidence["last_milvus_index"] = {
        "receipt_id": receipt.get("receipt_id"),
        "status": receipt.get("status"),
        "snapshot_hash": receipt.get("snapshot_hash"),
        "index_digest": receipt.get("index_digest"),
        "physical_collection": receipt.get("physical_collection"),
        "counts": receipt.get("counts") or {},
        "created_at": receipt.get("created_at"),
    }
    manifest["evidence"] = evidence
    manifest["updated_at"] = deal_store.utc_now_iso()
    deal_store.write_json(manifest_path, manifest)


def _write_receipt(
    package_dir: Path,
    receipt: dict[str, Any],
    *,
    wiki_root: Path | str | None,
) -> dict[str, Any]:
    deal_store.write_json(package_dir / MILVUS_INDEX_RECEIPT_PATH, receipt)
    _sync_manifest(package_dir, receipt)
    deal_store.append_audit_event(
        str(receipt["deal_id"]),
        {
            "event_type": "deal_evidence_milvus_indexed",
            "receipt_id": receipt.get("receipt_id"),
            "status": receipt.get("status"),
            "snapshot_hash": receipt.get("snapshot_hash"),
            "index_digest": receipt.get("index_digest"),
            "physical_collection": receipt.get("physical_collection"),
            "counts": receipt.get("counts") or {},
            "created_by": receipt.get("created_by"),
        },
        wiki_root=wiki_root,
    )
    return receipt


def _receipt(
    *,
    deal_id: str,
    status: str,
    snapshot_hash: str,
    index_digest: str,
    physical_collection: str,
    vector_field: str,
    dimensions: int,
    item_count: int,
    existing_count: int,
    inserted: int,
    deleted: int,
    created_by: dict[str, Any] | None,
    error: str | None = None,
) -> dict[str, Any]:
    receipt_id = "PMMILVUS-" + _stable_hash(
        {
            "deal_id": deal_id,
            "snapshot_hash": snapshot_hash,
            "index_digest": index_digest,
            "physical_collection": physical_collection,
        }
    )[:24].upper()
    payload = {
        "schema_version": MILVUS_INDEX_RECEIPT_SCHEMA,
        "receipt_id": receipt_id,
        "status": status,
        "deal_id": deal_id,
        "project_tag": deal_id,
        "logical_collection": vector_retrieval.SHARED_DEAL_COLLECTION,
        "physical_collection": physical_collection,
        "snapshot_hash": snapshot_hash,
        "index_digest": index_digest,
        "embedding_model": _embedding_model(),
        "vector_field": vector_field,
        "vector_dimensions": dimensions,
        "counts": {
            "items": item_count,
            "existing": existing_count,
            "inserted": inserted,
            "deleted": deleted,
        },
        "created_at": deal_store.utc_now_iso(),
        "created_by": created_by,
    }
    if error:
        payload["error"] = error[:300]
    return payload


def index_deal_evidence_milvus(
    deal_id: str,
    *,
    created_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    package_dir = _require_package_dir(normalized_deal_id, wiki_root=wiki_root)
    lock_target = package_dir / "evidence" / "milvus_index.operation"
    with deal_store._locked_path(lock_target):
        return _index_deal_evidence_milvus_locked(
            normalized_deal_id,
            package_dir=package_dir,
            created_by=created_by,
            wiki_root=wiki_root,
            timeout=timeout,
        )


def remove_deal_document_rows(
    deal_id: str,
    document_id: str,
    *,
    deleted_by: dict[str, Any] | None = None,
    wiki_root: Path | str | None = None,
) -> dict[str, Any]:
    """Remove one deleted material's rows without disturbing other Deal evidence."""

    normalized_deal_id = deal_store.validate_deal_id(deal_id)
    normalized_document_id = str(document_id or "").strip().upper()
    if not _DOCUMENT_ID_RE.fullmatch(normalized_document_id):
        raise ValueError("document_id_invalid")
    package_dir = _require_package_dir(normalized_deal_id, wiki_root=wiki_root)
    lock_target = package_dir / "evidence" / "milvus_index.operation"
    with deal_store._locked_path(lock_target):
        collection = _open_collection()
        contract = _collection_contract(collection)
        rows = _query_existing_rows(
            collection,
            primary_field=contract["primary_field"],
            deal_id=normalized_deal_id,
        )
        matching_rows = [
            row
            for row in rows
            if str(_metadata(row.get("metadata")).get("document_id") or "")
            == normalized_document_id
        ]
        deleted = _delete_rows(
            collection,
            primary_field=contract["primary_field"],
            rows=matching_rows,
        )
        remaining = _query_existing_rows(
            collection,
            primary_field=contract["primary_field"],
            deal_id=normalized_deal_id,
        )
        if any(
            str(_metadata(row.get("metadata")).get("document_id") or "")
            == normalized_document_id
            for row in remaining
        ):
            raise RuntimeError("milvus_document_cleanup_failed")
        result = {
            "status": "cleaned" if deleted else "unchanged",
            "deal_id": normalized_deal_id,
            "document_id": normalized_document_id,
            "physical_collection": _physical_collection(),
            "matched": len(matching_rows),
            "deleted": deleted,
            "remaining_project_rows": len(remaining),
        }
        deal_store.append_audit_event(
            normalized_deal_id,
            {
                "event_type": "deal_document_milvus_rows_removed",
                **result,
                "deleted_by": deleted_by,
            },
            wiki_root=wiki_root,
        )
        return result


def _index_deal_evidence_milvus_locked(
    normalized_deal_id: str,
    *,
    package_dir: Path,
    created_by: dict[str, Any] | None,
    wiki_root: Path | str | None,
    timeout: float,
) -> dict[str, Any]:
    snapshot = _read_snapshot(package_dir, normalized_deal_id)
    snapshot_hash = str(snapshot["snapshot_hash"])
    items = _read_evidence_items(package_dir / "evidence" / "evidence_items.ndjson")
    records, content_index_digest = _build_records(
        items,
        deal_id=normalized_deal_id,
        snapshot_hash=snapshot_hash,
    )
    index_digest = content_index_digest
    physical_collection = _physical_collection()
    collection: Any | None = None
    contract: dict[str, Any] = {}
    existing_rows: list[dict[str, Any]] = []
    inserted_count = 0
    deleted_count = 0
    try:
        collection = _open_collection()
        contract = _collection_contract(collection)
        index_digest = _bind_embedding_contract(
            records,
            content_index_digest=content_index_digest,
            embedding_model=_embedding_model(),
            vector_field=contract["vector_field"],
            vector_dimensions=contract["dimensions"],
        )
        existing_rows = _query_existing_rows(
            collection,
            primary_field=contract["primary_field"],
            deal_id=normalized_deal_id,
        )
        if _existing_rows_match(
            existing_rows,
            records=records,
            deal_id=normalized_deal_id,
            snapshot_hash=snapshot_hash,
            index_digest=index_digest,
        ):
            _assert_snapshot_current(package_dir, normalized_deal_id, snapshot_hash)
            return _write_receipt(
                package_dir,
                _receipt(
                    deal_id=normalized_deal_id,
                    status="unchanged",
                    snapshot_hash=snapshot_hash,
                    index_digest=index_digest,
                    physical_collection=physical_collection,
                    vector_field=contract["vector_field"],
                    dimensions=contract["dimensions"],
                    item_count=len(records),
                    existing_count=len(existing_rows),
                    inserted=0,
                    deleted=0,
                    created_by=created_by,
                ),
                wiki_root=wiki_root,
            )

        if not records:
            _assert_snapshot_current(package_dir, normalized_deal_id, snapshot_hash)
            deleted_count = _delete_rows(
                collection,
                primary_field=contract["primary_field"],
                rows=existing_rows,
            )
            indexed_rows = _query_existing_rows(
                collection,
                primary_field=contract["primary_field"],
                deal_id=normalized_deal_id,
            )
            if indexed_rows:
                raise RuntimeError("milvus_empty_snapshot_cleanup_failed")
            _assert_snapshot_current(package_dir, normalized_deal_id, snapshot_hash)
            return _write_receipt(
                package_dir,
                _receipt(
                    deal_id=normalized_deal_id,
                    status="indexed",
                    snapshot_hash=snapshot_hash,
                    index_digest=index_digest,
                    physical_collection=physical_collection,
                    vector_field=contract["vector_field"],
                    dimensions=contract["dimensions"],
                    item_count=0,
                    existing_count=len(existing_rows),
                    inserted=0,
                    deleted=deleted_count,
                    created_by=created_by,
                ),
                wiki_root=wiki_root,
            )

        vectors = _embed_texts(
            [record["text"] for record in records],
            dimensions=contract["dimensions"],
            timeout=timeout,
        )
        if len(vectors) != len(records):
            raise ValueError("embedding_record_count_mismatch")
        _assert_snapshot_current(package_dir, normalized_deal_id, snapshot_hash)

        _insert_records(
            collection,
            contract=contract,
            records=records,
            vectors=vectors,
        )
        inserted_count = len(records)
        deleted_count = _delete_rows(
            collection,
            primary_field=contract["primary_field"],
            rows=existing_rows,
        )
        indexed_rows = _query_existing_rows(
            collection,
            primary_field=contract["primary_field"],
            deal_id=normalized_deal_id,
        )
        if not _existing_rows_match(
            indexed_rows,
            records=records,
            deal_id=normalized_deal_id,
            snapshot_hash=snapshot_hash,
            index_digest=index_digest,
        ):
            raise RuntimeError("milvus_post_write_verification_failed")
        _assert_snapshot_current(package_dir, normalized_deal_id, snapshot_hash)
        return _write_receipt(
            package_dir,
            _receipt(
                deal_id=normalized_deal_id,
                status="indexed",
                snapshot_hash=snapshot_hash,
                index_digest=index_digest,
                physical_collection=physical_collection,
                vector_field=contract["vector_field"],
                dimensions=contract["dimensions"],
                item_count=len(records),
                existing_count=len(existing_rows),
                inserted=inserted_count,
                deleted=deleted_count,
                created_by=created_by,
            ),
            wiki_root=wiki_root,
        )
    except Exception as exc:
        if isinstance(exc, DealEvidenceMilvusIndexError):
            raise
        if contract:
            failed_receipt = _receipt(
                deal_id=normalized_deal_id,
                status="failed",
                snapshot_hash=snapshot_hash,
                index_digest=index_digest,
                physical_collection=physical_collection,
                vector_field=str(contract.get("vector_field") or ""),
                dimensions=int(contract.get("dimensions") or 0),
                item_count=len(records),
                existing_count=len(existing_rows),
                inserted=inserted_count,
                deleted=deleted_count,
                created_by=created_by,
                error=f"{type(exc).__name__}: {exc}",
            )
            try:
                _write_receipt(package_dir, failed_receipt, wiki_root=wiki_root)
            except Exception:
                pass
        raise DealEvidenceMilvusIndexError(str(exc), receipt=failed_receipt if contract else None) from exc
