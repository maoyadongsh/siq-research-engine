#!/usr/bin/env python3
"""Validate and optionally ingest versioned IC profile methodology knowledge."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = (
    PROJECT_ROOT
    / "agents"
    / "hermes"
    / "profiles"
    / "siq_ic_shared"
    / "knowledge"
    / "manifest.v1.json"
)
SUMMARY_SCHEMA = "siq_ic_profile_knowledge_ingest_summary_v1"
MANIFEST_SCHEMA = "siq_ic_profile_knowledge_manifest_v1"
MANAGED_BY = "siq_ingest_ic_profile_knowledge_v1"
DEFAULT_EMBED_MODEL = "Qwen3-VL-Embedding-2B"
PROFILE_IDS = (
    "siq_ic_master_coordinator",
    "siq_ic_chairman",
    "siq_ic_strategist",
    "siq_ic_sector_expert",
    "siq_ic_finance_auditor",
    "siq_ic_legal_scanner",
    "siq_ic_risk_controller",
)
PROJECT_TAG_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
FORBIDDEN_ASSET_PATTERNS = (
    re.compile(r"(?:^|\s)/(?:home|Users)/"),
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"(?i)(?:api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*\S+"),
    re.compile(r"(?i)(?:memory|sessions?)/"),
)


class ManifestValidationError(ValueError):
    pass


class SchemaCompatibilityError(RuntimeError):
    pass


class EmbeddingConfigurationError(RuntimeError):
    pass


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def manifest_digest(payload: dict[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "manifest_digest"}
    if isinstance(body.get("profiles"), list):
        body["profiles"] = [
            {key: value for key, value in entry.items() if key != "asset_path"}
            if isinstance(entry, dict)
            else entry
            for entry in body["profiles"]
        ]
    return sha256_text(canonical_json(body))


def repo_relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError as exc:
        raise ManifestValidationError("knowledge_asset_outside_repository") from exc


def _safe_asset_path(manifest_path: Path, raw_path: Any) -> Path:
    relative = Path(str(raw_path or ""))
    if not relative.name or relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".md":
        raise ManifestValidationError("knowledge_asset_path_invalid")
    resolved = (manifest_path.parent / relative).resolve()
    try:
        resolved.relative_to(manifest_path.parent.resolve())
    except ValueError as exc:
        raise ManifestValidationError("knowledge_asset_path_escape") from exc
    return resolved


def load_and_validate_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestValidationError("manifest_missing") from exc
    except json.JSONDecodeError as exc:
        raise ManifestValidationError("manifest_json_invalid") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != MANIFEST_SCHEMA:
        raise ManifestValidationError("manifest_schema_invalid")
    if payload.get("manifest_digest") != manifest_digest(payload):
        raise ManifestValidationError("manifest_digest_mismatch")
    if payload.get("source_class") != "background_knowledge":
        raise ManifestValidationError("manifest_source_class_invalid")
    if not str(payload.get("contract_version") or "").strip():
        raise ManifestValidationError("manifest_contract_version_missing")
    if not str(payload.get("manifest_version") or "").strip():
        raise ManifestValidationError("manifest_version_missing")
    project_tag = str(payload.get("project_tag") or "")
    if not PROJECT_TAG_RE.fullmatch(project_tag):
        raise ManifestValidationError("manifest_project_tag_invalid")
    try:
        vector_dimensions = int(payload.get("vector_dimensions"))
    except (TypeError, ValueError) as exc:
        raise ManifestValidationError("manifest_vector_dimensions_invalid") from exc
    if vector_dimensions <= 0:
        raise ManifestValidationError("manifest_vector_dimensions_invalid")
    chunking = payload.get("chunking") if isinstance(payload.get("chunking"), dict) else {}
    max_chars = int(chunking.get("max_chars") or 0)
    overlap_chars = int(chunking.get("overlap_chars") or 0)
    if max_chars < 200 or overlap_chars < 0 or overlap_chars >= max_chars:
        raise ManifestValidationError("manifest_chunking_invalid")

    profiles = payload.get("profiles")
    if not isinstance(profiles, list) or len(profiles) != len(PROFILE_IDS):
        raise ManifestValidationError("manifest_profile_count_invalid")
    observed_ids: list[str] = []
    observed_collections: list[str] = []
    observed_paths: list[str] = []
    normalized_profiles: list[dict[str, Any]] = []
    for entry in profiles:
        if not isinstance(entry, dict):
            raise ManifestValidationError("manifest_profile_entry_invalid")
        profile_id = str(entry.get("profile_id") or "")
        collection = str(entry.get("physical_collection") or "")
        title = str(entry.get("title") or "").strip()
        asset_path = _safe_asset_path(path, entry.get("path"))
        if not asset_path.is_file():
            raise ManifestValidationError("knowledge_asset_missing")
        text = asset_path.read_text(encoding="utf-8")
        if not text.strip():
            raise ManifestValidationError("knowledge_asset_empty")
        if any(pattern.search(text) for pattern in FORBIDDEN_ASSET_PATTERNS):
            raise ManifestValidationError("knowledge_asset_forbidden_content")
        if sha256_text(text) != str(entry.get("sha256") or ""):
            raise ManifestValidationError("knowledge_asset_digest_mismatch")
        if not title or not collection:
            raise ManifestValidationError("manifest_profile_metadata_missing")
        observed_ids.append(profile_id)
        observed_collections.append(collection)
        observed_paths.append(asset_path.name)
        normalized_profiles.append({**entry, "asset_path": asset_path})
    if set(observed_ids) != set(PROFILE_IDS) or len(set(observed_ids)) != len(PROFILE_IDS):
        raise ManifestValidationError("manifest_profile_ids_invalid")
    if len(set(observed_collections)) != len(PROFILE_IDS):
        raise ManifestValidationError("manifest_private_collections_not_distinct")
    if len(set(observed_paths)) != len(PROFILE_IDS):
        raise ManifestValidationError("manifest_asset_paths_not_distinct")
    return {**payload, "profiles": normalized_profiles}


def _split_long_text(text: str, *, max_chars: int, overlap_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text.strip()] if text.strip() else []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", start, end), text.rfind(". ", start, end))
            if boundary > start + max_chars // 2:
                end = boundary + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return chunks


def split_markdown(text: str, *, max_chars: int, overlap_chars: int) -> list[dict[str, str]]:
    sections: list[tuple[str, list[str]]] = []
    title = "Overview"
    lines: list[str] = []
    for line in text.strip().splitlines():
        if line.startswith("## "):
            if any(item.strip() for item in lines):
                sections.append((title, lines))
            title = line[3:].strip() or "Section"
            lines = [line]
        else:
            lines.append(line)
    if any(item.strip() for item in lines):
        sections.append((title, lines))

    chunks: list[dict[str, str]] = []
    for section_title, section_lines in sections:
        section = "\n".join(section_lines).strip()
        for chunk in _split_long_text(section, max_chars=max_chars, overlap_chars=overlap_chars):
            chunks.append({"section_title": section_title, "text": chunk})
    return chunks


def build_records(manifest: dict[str, Any], entries: list[dict[str, Any]], *, embed_model: str) -> list[dict[str, Any]]:
    chunking = manifest["chunking"]
    records: list[dict[str, Any]] = []
    for entry in entries:
        asset_path = Path(entry["asset_path"])
        source_path = repo_relative(asset_path)
        chunks = split_markdown(
            asset_path.read_text(encoding="utf-8"),
            max_chars=int(chunking["max_chars"]),
            overlap_chars=int(chunking["overlap_chars"]),
        )
        for chunk_index, chunk in enumerate(chunks, start=1):
            content = chunk["text"]
            content_hash = sha256_text(content)
            knowledge_id = "ICKB-" + sha256_text(
                "\x1f".join(
                    [
                        str(manifest["contract_version"]),
                        str(manifest["manifest_version"]),
                        str(entry["profile_id"]),
                        source_path,
                        str(chunk_index),
                        content_hash,
                    ]
                )
            )[:32].upper()
            record_digest = sha256_text(
                "\x1f".join(
                    [
                        knowledge_id,
                        content_hash,
                        str(manifest["manifest_digest"]),
                        embed_model,
                        str(manifest["vector_dimensions"]),
                    ]
                )
            )
            title = f"{entry['title']} - {chunk['section_title']}"
            metadata = {
                "schema_version": "siq_ic_profile_knowledge_chunk_v1",
                "knowledge_id": knowledge_id,
                "record_digest": record_digest,
                "source_class": "background_knowledge",
                "knowledge_type": "methodology",
                "profile": entry["profile_id"],
                "profile_id": entry["profile_id"],
                "physical_collection": entry["physical_collection"],
                "role": entry["role"],
                "contract_version": manifest["contract_version"],
                "manifest_version": manifest["manifest_version"],
                "manifest_digest": manifest["manifest_digest"],
                "project_tag": manifest["project_tag"],
                "source": source_path,
                "source_path": source_path,
                "title": title,
                "section_title": chunk["section_title"],
                "chunk_index": chunk_index,
                "content_hash": content_hash,
                "text": content,
                "text_len": len(content),
                "embedding_model": embed_model,
                "vector_dimensions": int(manifest["vector_dimensions"]),
                "project_fact": False,
                "managed_by": MANAGED_BY,
            }
            records.append(
                {
                    "knowledge_id": knowledge_id,
                    "record_digest": record_digest,
                    "profile_id": entry["profile_id"],
                    "physical_collection": entry["physical_collection"],
                    "project_tag": manifest["project_tag"],
                    "title": title,
                    "text": content,
                    "metadata": metadata,
                }
            )
    return records


def configured_embedding_base(args: argparse.Namespace) -> str:
    return str(
        args.embed_url
        or os.getenv("SIQ_IC_KNOWLEDGE_EMBEDDING_BASE_URL")
        or os.getenv("SIQ_EMBEDDING_BASE_URL")
        or os.getenv("EMBEDDING_BASE_URL")
        or ""
    ).strip()


def embedding_endpoint(args: argparse.Namespace) -> str:
    base = configured_embedding_base(args).rstrip("/")
    if not base:
        raise EmbeddingConfigurationError("embedding_endpoint_not_configured")
    if base.endswith("/v1/embeddings") or base.endswith("/embeddings"):
        return base
    if base.endswith("/v1"):
        return base + "/embeddings"
    return base + "/v1/embeddings"


def embed_batch(
    texts: list[str],
    *,
    endpoint: str,
    model: str,
    dimensions: int,
    timeout: float,
) -> list[list[float]]:
    headers = {"Content-Type": "application/json"}
    api_key = (
        os.getenv("SIQ_IC_KNOWLEDGE_EMBEDDING_API_KEY")
        or os.getenv("SIQ_EMBEDDING_API_KEY")
        or os.getenv("EMBEDDING_API_KEY")
    )
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    with httpx.Client() as client:
        response = client.post(
            endpoint,
            headers=headers,
            json={"model": model, "input": texts},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or len(data) != len(texts):
        raise RuntimeError("embedding_response_size_mismatch")
    vectors_by_index: dict[int, list[float]] = {}
    for fallback_index, item in enumerate(data):
        if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
            raise RuntimeError("embedding_response_item_invalid")
        raw_vector = [float(value) for value in item["embedding"]]
        if len(raw_vector) != dimensions:
            raise RuntimeError("embedding_dimensions_mismatch")
        norm = math.sqrt(sum(value * value for value in raw_vector))
        if norm <= 1e-12:
            raise RuntimeError("embedding_zero_norm")
        index = item.get("index")
        vectors_by_index[int(index) if isinstance(index, int) else fallback_index] = [
            value / norm for value in raw_vector
        ]
    try:
        return [vectors_by_index[index] for index in range(len(texts))]
    except KeyError as exc:
        raise RuntimeError("embedding_response_index_invalid") from exc


def _dtype_name(field: Any) -> str:
    dtype = getattr(field, "dtype", None)
    return str(getattr(dtype, "name", "") or dtype or "").upper()


def validate_collection_schema(collection: Any, *, dimensions: int) -> dict[str, Any]:
    schema = getattr(collection, "schema", None)
    fields = {
        str(getattr(field, "name", "") or ""): field
        for field in list(getattr(schema, "fields", None) or [])
    }
    if not all(name in fields for name in ("id", "vector", "project_tag", "metadata")):
        raise SchemaCompatibilityError("collection_required_fields_missing")
    if "INT64" not in _dtype_name(fields["id"]):
        raise SchemaCompatibilityError("collection_id_type_invalid")
    if getattr(fields["id"], "is_primary", False) is not True or getattr(fields["id"], "auto_id", False) is not True:
        raise SchemaCompatibilityError("collection_id_contract_invalid")
    if "FLOAT_VECTOR" not in _dtype_name(fields["vector"]):
        raise SchemaCompatibilityError("collection_vector_type_invalid")
    vector_params = getattr(fields["vector"], "params", None)
    vector_params = vector_params if isinstance(vector_params, dict) else {}
    if int(vector_params.get("dim") or 0) != dimensions:
        raise SchemaCompatibilityError("collection_vector_dimensions_invalid")
    if "VARCHAR" not in _dtype_name(fields["project_tag"]):
        raise SchemaCompatibilityError("collection_project_tag_type_invalid")
    if "JSON" not in _dtype_name(fields["metadata"]):
        raise SchemaCompatibilityError("collection_metadata_type_invalid")

    metric_type = ""
    index_type = ""
    for index in list(getattr(collection, "indexes", None) or []):
        if str(getattr(index, "field_name", "") or "") != "vector":
            continue
        params = getattr(index, "params", None)
        params = params if isinstance(params, dict) else {}
        metric_type = str(params.get("metric_type") or "").upper()
        index_type = str(params.get("index_type") or "").upper()
        break
    if not metric_type or not index_type:
        raise SchemaCompatibilityError("collection_vector_index_missing")
    if metric_type not in {"L2", "IP", "COSINE"}:
        raise SchemaCompatibilityError("collection_vector_metric_invalid")
    return {"metric_type": metric_type, "index_type": index_type}


def open_collection(args: argparse.Namespace, collection_name: str) -> Any:
    from pymilvus import Collection, connections, utility  # type: ignore[import-not-found]

    alias = "siq_ic_profile_knowledge"
    connections.connect(
        alias=alias,
        host=args.milvus_host,
        port=args.milvus_port,
        db_name=args.milvus_db,
    )
    if not utility.has_collection(collection_name, using=alias):
        raise SchemaCompatibilityError("collection_missing")
    return Collection(collection_name, using=alias)


def _metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
    return {}


def read_managed_rows(collection: Any, *, project_tag: str, profile_id: str) -> list[dict[str, Any]]:
    if not PROJECT_TAG_RE.fullmatch(project_tag):
        raise ValueError("project_tag_invalid")
    collection.load()
    rows = collection.query(
        expr=f'project_tag == "{project_tag}"',
        output_fields=["id", "metadata"],
        limit=16384,
    )
    return [
        {"id": row.get("id"), "metadata": metadata}
        for row in rows
        if isinstance(row, dict)
        and (metadata := _metadata(row.get("metadata"))).get("managed_by") == MANAGED_BY
        and metadata.get("profile_id") == profile_id
    ]


def build_reconcile_plan(records: list[dict[str, Any]], existing_rows: list[dict[str, Any]]) -> dict[str, Any]:
    desired_by_id = {record["knowledge_id"]: record for record in records}
    existing_by_id: dict[str, list[dict[str, Any]]] = {}
    for row in existing_rows:
        knowledge_id = str(row["metadata"].get("knowledge_id") or "")
        if knowledge_id:
            existing_by_id.setdefault(knowledge_id, []).append(row)

    insert_records: list[dict[str, Any]] = []
    delete_ids: list[int] = []
    skipped = 0
    for knowledge_id, record in desired_by_id.items():
        current = existing_by_id.get(knowledge_id, [])
        matching = [row for row in current if row["metadata"].get("record_digest") == record["record_digest"]]
        if matching:
            skipped += 1
            keep_id = matching[0].get("id")
            delete_ids.extend(
                int(row["id"])
                for row in current
                if row.get("id") is not None and row.get("id") != keep_id
            )
        else:
            insert_records.append(record)
            delete_ids.extend(int(row["id"]) for row in current if row.get("id") is not None)
    for knowledge_id, rows in existing_by_id.items():
        if knowledge_id not in desired_by_id:
            delete_ids.extend(int(row["id"]) for row in rows if row.get("id") is not None)
    return {
        "insert_records": insert_records,
        "delete_ids": sorted(set(delete_ids)),
        "skipped": skipped,
    }


def apply_reconcile_plan(
    collection: Any,
    plan: dict[str, Any],
    vectors_by_id: dict[str, list[float]],
) -> dict[str, int]:
    insert_records = plan["insert_records"]
    delete_ids = plan["delete_ids"]
    if insert_records:
        collection.insert(
            [
                [vectors_by_id[record["knowledge_id"]] for record in insert_records],
                [record["project_tag"] for record in insert_records],
                [record["metadata"] for record in insert_records],
            ]
        )
        collection.flush()
    if delete_ids:
        collection.delete(expr="id in [" + ",".join(str(value) for value in delete_ids) + "]")
        collection.flush()
    return {
        "inserted": len(insert_records),
        "deleted": len(delete_ids),
        "skipped": int(plan["skipped"]),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--profile", help="One canonical siq_ic_* profile ID")
    selection.add_argument("--all", action="store_true", help="Process all seven IC profiles")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--write", action="store_true", help="Connect to embedding and Milvus and reconcile records")
    parser.add_argument("--embed-url", default="", help="Embedding base URL or /v1/embeddings endpoint")
    parser.add_argument(
        "--embed-model",
        default=os.getenv("SIQ_IC_KNOWLEDGE_EMBEDDING_MODEL")
        or os.getenv("SIQ_EMBEDDING_MODEL")
        or DEFAULT_EMBED_MODEL,
    )
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--milvus-host", default=os.getenv("SIQ_MILVUS_HOST") or "127.0.0.1")
    parser.add_argument("--milvus-port", default=os.getenv("SIQ_MILVUS_PORT") or "19530")
    parser.add_argument("--milvus-db", default=os.getenv("SIQ_MILVUS_DB_NAME") or "default")
    parser.add_argument("--output", default="", help="Optional local JSON summary path")
    return parser.parse_args(argv)


def _selected_entries(manifest: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.all:
        return list(manifest["profiles"])
    selected = [entry for entry in manifest["profiles"] if entry["profile_id"] == args.profile]
    if not selected:
        raise ManifestValidationError("selected_profile_unknown")
    return selected


def write_summary(args: argparse.Namespace, summary: dict[str, Any]) -> None:
    serialized = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


def _base_summary(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema_version": SUMMARY_SCHEMA,
        "passed": False,
        "write": bool(args.write),
        "dry_run": not bool(args.write),
        "embedding_endpoint_configured": bool(configured_embedding_base(args)),
        "embed_model": args.embed_model,
        "selected_profiles": [args.profile] if args.profile else list(PROFILE_IDS),
        "planned_chunks": 0,
        "inserted": 0,
        "deleted": 0,
        "skipped": 0,
        "profiles": [],
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = _base_summary(args)
    try:
        manifest = load_and_validate_manifest(Path(args.manifest))
        entries = _selected_entries(manifest, args)
        records = build_records(manifest, entries, embed_model=args.embed_model)
        records_by_profile = {
            entry["profile_id"]: [record for record in records if record["profile_id"] == entry["profile_id"]]
            for entry in entries
        }
        summary.update(
            {
                "manifest_version": manifest["manifest_version"],
                "manifest_digest": manifest["manifest_digest"],
                "contract_version": manifest["contract_version"],
                "selected_profiles": [entry["profile_id"] for entry in entries],
                "planned_chunks": len(records),
                "profiles": [
                    {
                        "profile_id": entry["profile_id"],
                        "physical_collection": entry["physical_collection"],
                        "planned_chunks": len(records_by_profile[entry["profile_id"]]),
                    }
                    for entry in entries
                ],
            }
        )
        if not args.write:
            write_summary(args, {**summary, "passed": True})
            return 0

        endpoint = embedding_endpoint(args)
        dimensions = int(manifest["vector_dimensions"])
        collections: dict[str, Any] = {}
        plans: dict[str, dict[str, Any]] = {}

        # Validate every selected target before embedding or mutating any collection.
        for entry in entries:
            profile_id = entry["profile_id"]
            collection = open_collection(args, entry["physical_collection"])
            validate_collection_schema(collection, dimensions=dimensions)
            existing = read_managed_rows(
                collection,
                project_tag=manifest["project_tag"],
                profile_id=profile_id,
            )
            collections[profile_id] = collection
            plans[profile_id] = build_reconcile_plan(records_by_profile[profile_id], existing)

        # Complete all embedding calls before the first Milvus mutation.
        vectors_by_id: dict[str, list[float]] = {}
        pending = [record for profile_id in records_by_profile for record in plans[profile_id]["insert_records"]]
        batch_size = max(1, min(int(args.batch_size), 64))
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset : offset + batch_size]
            vectors = embed_batch(
                [record["text"] for record in batch],
                endpoint=endpoint,
                model=args.embed_model,
                dimensions=dimensions,
                timeout=args.timeout,
            )
            vectors_by_id.update(
                {record["knowledge_id"]: vector for record, vector in zip(batch, vectors, strict=True)}
            )

        profile_results = []
        totals = {"inserted": 0, "deleted": 0, "skipped": 0}
        summary["profiles"] = profile_results
        for entry in entries:
            profile_id = entry["profile_id"]
            result = apply_reconcile_plan(collections[profile_id], plans[profile_id], vectors_by_id)
            for key in totals:
                totals[key] += result[key]
                summary[key] = totals[key]
            profile_results.append(
                {
                    "profile_id": profile_id,
                    "physical_collection": entry["physical_collection"],
                    "planned_chunks": len(records_by_profile[profile_id]),
                    **result,
                }
            )
        write_summary(args, {**summary, **totals, "profiles": profile_results, "passed": True})
        return 0
    except Exception as exc:
        write_summary(
            args,
            {
                **summary,
                "passed": False,
                "error_type": type(exc).__name__,
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
