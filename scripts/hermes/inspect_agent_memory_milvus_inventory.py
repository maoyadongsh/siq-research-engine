#!/usr/bin/env python3
# isort: skip_file
"""Collect a redacted, read-only Agent Memory Milvus inventory.

The profile-file seed contract is the only supported way to classify legacy
v1 records as unscoped.  This command never mutates Milvus and emits a
planner-compatible snapshot; any unexpected row is fail-closed as unknown.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = PROJECT_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import agent_memory_milvus  # noqa: E402


INVENTORY_SCHEMA = "siq_agent_memory_milvus_readonly_inventory_v2"
SNAPSHOT_SCHEMA = "siq_agent_memory_milvus_snapshot_v1"
EXPECTED_PROFILE_SCHEMA = "siq_agent_profile_chunk_v1"
EXPECTED_SOURCE_KIND = "profile_file"
EXPECTED_MEMORY_TYPE = "profile_file"
EXPECTED_VISIBILITY = "system_shared"
EXPECTED_ID_PREFIX = "profile_file"
LEGACY_FIELDS = agent_memory_milvus.REQUIRED_FIELDS - set(agent_memory_milvus.RESEARCH_IDENTITY_FIELDS)
IDENTITY_FIELDS = ("market", "company_id", "filing_id", "parse_run_id")
DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts/eval-runs/local/agent-memory-milvus-profile-inventory.json"


def _counter(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(collections.Counter(str(row.get(field) or "") for row in rows).items()))


def _manifest_sha256(rows: list[dict[str, Any]]) -> str:
    pairs = sorted(
        (str(row.get("id") or ""), str(row.get("content_hash") or ""))
        for row in rows
    )
    payload = json.dumps(pairs, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _metadata_probe(rows: list[dict[str, Any]]) -> dict[str, Any]:
    schemas: collections.Counter[str] = collections.Counter()
    malformed = 0
    research_identity_absent = 0
    research_identity_complete = 0
    research_identity_partial = 0
    for row in rows:
        try:
            metadata = json.loads(str(row.get("metadata_json") or "{}"))
        except (TypeError, ValueError):
            malformed += 1
            continue
        if not isinstance(metadata, dict):
            malformed += 1
            continue
        schemas[str(metadata.get("schema_version") or "")] += 1
        identity = metadata.get("research_identity")
        if not identity:
            research_identity_absent += 1
            continue
        if isinstance(identity, dict) and all(str(identity.get(field) or "").strip() for field in IDENTITY_FIELDS):
            research_identity_complete += 1
        else:
            research_identity_partial += 1
    return {
        "schema_counts": dict(sorted(schemas.items())),
        "malformed_or_non_object_count": malformed,
        "research_identity_absent_count": research_identity_absent,
        "research_identity_complete_count": research_identity_complete,
        "research_identity_partial_count": research_identity_partial,
        "raw_metadata_included": False,
    }


def _field_details(client: Any, collection: str) -> list[dict[str, Any]]:
    description = client.describe_collection(collection)
    raw_fields = description.get("fields") if isinstance(description, dict) else []
    details: list[dict[str, Any]] = []
    for field in raw_fields or []:
        if not isinstance(field, dict):
            continue
        detail = {"name": str(field.get("name") or "")}
        for key in ("data_type", "max_length", "dim", "dimension", "is_primary"):
            if key in field:
                detail[key if key != "dim" else "dimension"] = field[key]
        params = field.get("params") if isinstance(field.get("params"), dict) else {}
        if "dimension" not in detail and params.get("dim") is not None:
            detail["dimension"] = params["dim"]
        details.append(detail)
    return sorted(details, key=lambda item: item["name"])


def _index_probe(client: Any, collection: str) -> dict[str, Any]:
    try:
        raw_indexes = client.list_indexes(collection_name=collection)
    except TypeError:
        raw_indexes = client.list_indexes(collection)
    except Exception as exc:
        return {"error_type": type(exc).__name__, "indexes": []}
    raw_names = raw_indexes.get("index_names", []) if isinstance(raw_indexes, dict) else raw_indexes
    names = []
    for item in raw_names or []:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            names.append(str(item.get("index_name") or item.get("name") or ""))
    indexes: list[dict[str, Any]] = []
    for name in names:
        if not name:
            continue
        try:
            try:
                details = client.describe_index(collection_name=collection, index_name=name)
            except TypeError:
                details = client.describe_index(collection, name)
        except Exception as exc:
            indexes.append({"name": name, "error_type": type(exc).__name__})
            continue
        indexes.append({"name": name, "details": details})
    return {"indexes": indexes}


def _aliases(
    client: Any,
    collection: str,
    requested_aliases: list[str] | None = None,
) -> tuple[list[dict[str, str]], str | None]:
    try:
        try:
            raw = client.list_aliases(collection_name=collection)
        except TypeError:
            raw = client.list_aliases(collection)
    except Exception as exc:
        return [], type(exc).__name__
    aliases: list[dict[str, str]] = []
    describe_errors: list[str] = []
    seen_names: set[str] = set()
    raw_aliases = raw.get("aliases") if isinstance(raw, dict) else raw
    for item in raw_aliases or []:
        if isinstance(item, str):
            alias_name = item
            fallback_collection = str(raw.get("collection_name") or collection) if isinstance(raw, dict) else collection
        elif isinstance(item, dict):
            alias_name = str(item.get("alias_name") or item.get("name") or "")
            fallback_collection = str(item.get("collection_name") or item.get("collection") or "")
        else:
            continue
        if not alias_name:
            continue
        seen_names.add(alias_name)
        resolved_collection = fallback_collection
        try:
            try:
                description = client.describe_alias(alias=alias_name)
            except TypeError:
                description = client.describe_alias(alias_name)
            if isinstance(description, dict):
                resolved_collection = str(
                    description.get("collection_name")
                    or description.get("collection")
                    or resolved_collection
                )
        except Exception as exc:
            resolved_collection = ""
            describe_errors.append(type(exc).__name__)
        aliases.append({"name": alias_name, "collection": resolved_collection})
    for alias_name in requested_aliases or []:
        if not alias_name or alias_name in seen_names:
            continue
        try:
            try:
                description = client.describe_alias(alias=alias_name)
            except TypeError:
                description = client.describe_alias(alias_name)
            resolved_collection = str(
                description.get("collection_name") or description.get("collection") or ""
            ) if isinstance(description, dict) else ""
            aliases.append({"name": alias_name, "collection": resolved_collection})
        except Exception as exc:
            aliases.append({"name": alias_name, "collection": ""})
            describe_errors.append(type(exc).__name__)
    error = ",".join(sorted(set(describe_errors))) or None
    return aliases, error


def _query_inventory_rows(client: Any, collection: str, entity_count: int | None) -> list[dict[str, Any]]:
    output_fields = ["id", "content_hash", "source_kind", "memory_type", "visibility", "metadata_json"]
    if entity_count is not None and entity_count <= 16384:
        rows = client.query(
            collection_name=collection,
            filter='id != ""',
            output_fields=output_fields,
            limit=max(1, entity_count or 1),
        )
        return [row for row in rows if isinstance(row, dict)]

    if not hasattr(client, "query_iterator"):
        if entity_count is None:
            raise RuntimeError("Milvus collection stats are unavailable and query_iterator is unavailable")
        raise RuntimeError("Milvus inventory exceeds the query window and query_iterator is unavailable")
    iterator = client.query_iterator(
        collection_name=collection,
        filter='id != ""',
        output_fields=output_fields,
        batch_size=4096,
        limit=entity_count if entity_count is not None else -1,
    )
    rows: list[dict[str, Any]] = []
    try:
        while True:
            batch = iterator.next()
            if not batch:
                break
            rows.extend(row for row in batch if isinstance(row, dict))
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()
    return rows


def collect_inventory(
    client: Any,
    collection: str,
    requested_aliases: list[str] | None = None,
) -> dict[str, Any]:
    if not client.has_collection(collection):
        raise RuntimeError(f"Milvus collection does not exist: {collection}")
    details = _field_details(client, collection)
    fields = [str(item["name"]) for item in details]
    try:
        raw_stats = client.get_collection_stats(collection_name=collection)
    except TypeError:
        raw_stats = client.get_collection_stats(collection)
    except Exception:
        raw_stats = {}
    stats_count = raw_stats.get("row_count") if isinstance(raw_stats, dict) else None
    try:
        stats_count = int(stats_count) if stats_count is not None else None
    except (TypeError, ValueError):
        stats_count = None

    # Deliberately omit content and vectors; raw IDs/hashes are used only for an in-memory manifest.
    rows = _query_inventory_rows(client, collection, stats_count)
    entity_count = stats_count if stats_count is not None else len(rows)
    id_missing = sum(not str(row.get("id") or "").strip() for row in rows)
    hash_missing = sum(not str(row.get("content_hash") or "").strip() for row in rows)
    metadata = _metadata_probe(rows)
    source_kind = _counter(rows, "source_kind")
    memory_type = _counter(rows, "memory_type")
    visibility = _counter(rows, "visibility")
    id_prefix = dict(
        sorted(collections.Counter(str(row.get("id") or "").split(":", 1)[0] for row in rows).items())
    )
    contract_match = (
        len(rows) == entity_count
        and LEGACY_FIELDS <= set(fields)
        and not set(agent_memory_milvus.RESEARCH_IDENTITY_FIELDS).intersection(fields)
        and id_missing == 0
        and hash_missing == 0
        and source_kind == {EXPECTED_SOURCE_KIND: entity_count}
        and memory_type == {EXPECTED_MEMORY_TYPE: entity_count}
        and visibility == {EXPECTED_VISIBILITY: entity_count}
        and id_prefix == {EXPECTED_ID_PREFIX: entity_count}
        and metadata["malformed_or_non_object_count"] == 0
        and metadata["schema_counts"] == {EXPECTED_PROFILE_SCHEMA: entity_count}
        and metadata["research_identity_absent_count"] == entity_count
        and metadata["research_identity_complete_count"] == 0
        and metadata["research_identity_partial_count"] == 0
    )
    aliases, alias_error = _aliases(client, collection, requested_aliases)
    index_probe = _index_probe(client, collection)
    vector_field = next((item for item in details if item["name"] == agent_memory_milvus.VECTOR_FIELD), {})
    vector_dimension = int(vector_field.get("dimension") or 0)
    index_details = index_probe.get("indexes") or []
    first_index = next((item.get("details") for item in index_details if isinstance(item, dict) and item.get("details")), {})
    metric_type = str((first_index or {}).get("metric_type") or "")
    index_type = str((first_index or {}).get("index_type") or "")
    manifest_sha = _manifest_sha256(rows) if id_missing == 0 and hash_missing == 0 else ""
    contract_match = contract_match and vector_dimension > 0 and bool(metric_type) and bool(index_type) and bool(manifest_sha)
    observation_status = "observed" if contract_match else "unavailable"
    identity = {
        "observation_status": observation_status,
        "observation_reason": (
            "all records match the structured profile_file/system_shared seed contract"
            if contract_match
            else "legacy records did not satisfy the complete profile_file seed contract"
        ),
        "research_scoped_count": 0 if contract_match else None,
        "complete_count": 0 if contract_match else None,
        "partial_count": 0 if contract_match else None,
        "unscoped_count": entity_count if contract_match else None,
        "missing_by_field": {field: 0 if contract_match else None for field in IDENTITY_FIELDS},
    }
    return {
        "schema_version": SNAPSHOT_SCHEMA,
        "snapshot_kind": "redacted_read_only_inventory",
        "live_milvus_contacted": True,
        "writes_performed": False,
        "collection": {
            "name": collection,
            "declared_schema_version": "siq_agent_memory_milvus_v1" if set(agent_memory_milvus.RESEARCH_IDENTITY_FIELDS).isdisjoint(fields) else agent_memory_milvus.COLLECTION_SCHEMA_VERSION,
            "entity_count": entity_count,
            "fields": fields,
            "field_details": details,
            "vector_dimension": vector_dimension,
            "metric_type": metric_type,
            "index_type": index_type,
            "id_content_hash_manifest_sha256": manifest_sha,
        },
        "identity": identity,
        "aliases": aliases,
        "provenance": {
            "source_inventory_live_milvus_contacted": True,
            "raw_ids_or_content_hashes_included": False,
            "raw_metadata_content_or_vectors_included": False,
            "query_row_count": len(rows),
            "stats_row_count": stats_count,
            "id_missing_count": id_missing,
            "content_hash_missing_count": hash_missing,
            "source_kind_distribution": source_kind,
            "memory_type_distribution": memory_type,
            "visibility_distribution": visibility,
            "id_prefix_distribution": id_prefix,
            "metadata_probe": metadata,
            "index_probe": index_probe,
            "alias_error_type": alias_error,
            "contract_match": contract_match,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", default=agent_memory_milvus.DEFAULT_COLLECTION)
    parser.add_argument("--alias", action="append", default=[], help="Probe an alias even after it leaves the source collection.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--require-profile-contract", action="store_true")
    return parser


def main(argv: list[str] | None = None, *, client: Any | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = collect_inventory(client or agent_memory_milvus._client(), args.collection, args.alias)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"contract_match": report["provenance"]["contract_match"], "entity_count": report["collection"]["entity_count"]}, sort_keys=True))
    return 0 if not args.require_profile_contract or report["provenance"]["contract_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
