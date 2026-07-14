"""Optional Milvus vector retrieval adapter for SIQ Deal OS."""

from __future__ import annotations

import json
import os
import re
from importlib.util import find_spec
from typing import Any

import httpx

VECTOR_RETRIEVAL_SCHEMA = "siq_vector_retrieval_result_v1"
MAX_VECTOR_TOP_K = 50
DEFAULT_VECTOR_FIELD = "embedding"
DEFAULT_METRIC_TYPE = "COSINE"
DEFAULT_EMBEDDING_MODEL = "Qwen3-VL-Embedding-2B"
DEFAULT_TEXT_FIELDS = ("text", "content", "chunk", "quote", "claim")
DEFAULT_OUTPUT_FIELDS = ("text", "content", "metadata", "evidence_id", "document_id")
MANAGED_KNOWLEDGE_SCHEMA = "siq_ic_profile_knowledge_chunk_v1"
MANAGED_KNOWLEDGE_TYPE = "methodology"
MANAGED_KNOWLEDGE_WRITER = "siq_ingest_ic_profile_knowledge_v1"
DEFAULT_MANAGED_KNOWLEDGE_PROJECT_TAG = "siq-ic-profile-knowledge-2026-07-13-v1"
_PROJECT_TAG_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
DEFAULT_COLLECTION_ALIASES = {
    "siq_deal_shared": "ic_collaboration_shared",
    "siq_ic_master_coordinator": "ic_master_coordinator",
    "siq_ic_chairman": "ic_chairman",
    "siq_ic_strategist": "ic_strategist",
    "siq_ic_sector_expert": "ic_sector_expert",
    "siq_ic_finance_auditor": "ic_finance_auditor",
    "siq_ic_legal_scanner": "ic_legal_scanner",
    "siq_ic_risk_controller": "ic_risk_controller",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_top_k(value: int | str | None) -> int:
    try:
        parsed = int(value) if value is not None else 10
    except (TypeError, ValueError):
        parsed = 10
    return max(1, min(parsed, MAX_VECTOR_TOP_K))


def _collections(profile_id: str, configured: list[str] | tuple[str, ...] | None = None) -> list[str]:
    if configured:
        return [str(item).strip() for item in configured if str(item or "").strip()]
    raw = os.getenv("SIQ_MILVUS_COLLECTIONS") or ""
    if raw.strip():
        return [item.strip() for item in raw.split(",") if item.strip()]
    return ["siq_deal_shared", profile_id]


def _physical_collection(logical_name: str) -> str:
    env_key = "SIQ_MILVUS_COLLECTION_ALIAS_" + logical_name.upper().replace("-", "_")
    return str(os.getenv(env_key) or DEFAULT_COLLECTION_ALIASES.get(logical_name) or logical_name).strip()


def _round_robin_hits(hits_by_collection: dict[str, list[dict[str, Any]]], *, limit: int) -> list[dict[str, Any]]:
    """Keep one busy collection from starving another required knowledge source."""

    merged: list[dict[str, Any]] = []
    max_depth = max((len(items) for items in hits_by_collection.values()), default=0)
    for index in range(max_depth):
        for items in hits_by_collection.values():
            if index < len(items):
                merged.append(items[index])
                if len(merged) >= limit:
                    return merged
    return merged


def _embedding_endpoint() -> str:
    base = str(os.getenv("SIQ_EMBEDDING_BASE_URL") or os.getenv("EMBEDDING_BASE_URL") or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1"):
        return base + "/embeddings"
    return base + "/v1/embeddings"


def _embed_query(query: str, *, timeout: float) -> list[float]:
    endpoint = _embedding_endpoint()
    if not endpoint:
        raise ValueError("embedding_endpoint_not_configured")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = os.getenv("SIQ_EMBEDDING_API_KEY") or os.getenv("EMBEDDING_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    with httpx.Client() as client:
        response = client.post(
            endpoint,
            headers=headers,
            json={
                "model": (
                    os.getenv("SIQ_EMBEDDING_MODEL")
                    or os.getenv("EMBEDDING_MODEL")
                    or DEFAULT_EMBEDDING_MODEL
                ),
                "input": query,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(data, list) or not data:
        raise ValueError("embedding_response_empty")
    embedding = data[0].get("embedding") if isinstance(data[0], dict) else None
    if not isinstance(embedding, list):
        raise ValueError("embedding_response_invalid")
    return [float(item) for item in embedding]


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


def _managed_knowledge_project_tag() -> str:
    value = str(
        os.getenv("SIQ_IC_PROFILE_KNOWLEDGE_PROJECT_TAG")
        or DEFAULT_MANAGED_KNOWLEDGE_PROJECT_TAG
    ).strip()
    if not _PROJECT_TAG_RE.fullmatch(value):
        raise ValueError("managed_knowledge_project_tag_invalid")
    return value


def _is_managed_methodology_hit(hit: Any, *, profile_id: str, project_tag: str) -> bool:
    if not isinstance(hit, dict):
        return False
    metadata = _metadata(hit.get("metadata"))
    return (
        metadata.get("schema_version") == MANAGED_KNOWLEDGE_SCHEMA
        and metadata.get("knowledge_type") == MANAGED_KNOWLEDGE_TYPE
        and metadata.get("managed_by") == MANAGED_KNOWLEDGE_WRITER
        and metadata.get("profile_id") == profile_id
        and metadata.get("project_tag") == project_tag
        and str(hit.get("project_tag") or metadata.get("project_tag") or "") == project_tag
        and metadata.get("project_fact") is False
    )


def _hit_get(hit: Any, key: str) -> Any:
    entity = getattr(hit, "entity", None)
    if entity is not None:
        try:
            return entity.get(key)
        except (AttributeError, KeyError, TypeError):
            pass
    try:
        return hit.get(key)
    except (AttributeError, KeyError, TypeError):
        return None


def _normalize_hit(collection_name: str, hit: Any, index: int) -> dict[str, Any]:
    metadata = _metadata(_hit_get(hit, "metadata"))
    text = ""
    for field in DEFAULT_TEXT_FIELDS:
        value = _hit_get(hit, field) or metadata.get(field)
        if value:
            text = str(value)
            break
    distance = getattr(hit, "distance", None)
    if distance is None:
        distance = _hit_get(hit, "distance")
    return {
        "source_id": f"VEC-{collection_name}-{index + 1:03d}",
        "collection": collection_name,
        "evidence_id": metadata.get("evidence_id") or _hit_get(hit, "evidence_id"),
        "document_id": metadata.get("document_id") or _hit_get(hit, "document_id"),
        "project_tag": _hit_get(hit, "project_tag") or metadata.get("project_tag"),
        "title": metadata.get("title") or metadata.get("source") or collection_name,
        "text": text[:1200],
        "quote_preview": text[:300],
        "score": distance,
        "metadata": {
            key: value
            for key, value in metadata.items()
            if key not in {"api_key", "token", "secret", "password", "authorization"}
        },
    }


def _field_name(field: Any) -> str:
    return str(getattr(field, "name", "") or "").strip()


def _is_vector_field(field: Any) -> bool:
    dtype = getattr(field, "dtype", None)
    dtype_name = str(getattr(dtype, "name", "") or dtype or "").upper()
    return "VECTOR" in dtype_name


def _collection_search_config(collection: Any) -> dict[str, Any]:
    """Resolve search parameters from the collection's authoritative schema/index."""

    schema = getattr(collection, "schema", None)
    fields = list(getattr(schema, "fields", None) or [])
    fields_by_name = {
        name: field
        for field in fields
        if (name := _field_name(field))
    }
    configured_vector_field = str(os.getenv("SIQ_MILVUS_VECTOR_FIELD") or "").strip()
    vector_candidates = [configured_vector_field, DEFAULT_VECTOR_FIELD, "vector"]
    vector_candidates.extend(
        name for name, field in fields_by_name.items() if _is_vector_field(field)
    )
    vector_field = next(
        (
            name
            for name in vector_candidates
            if name and name in fields_by_name and _is_vector_field(fields_by_name[name])
        ),
        "",
    )
    if not vector_field:
        raise ValueError("milvus_vector_field_not_found")

    configured_output = os.getenv("SIQ_MILVUS_OUTPUT_FIELDS")
    requested_output_fields = [
        item.strip()
        for item in (configured_output.split(",") if configured_output is not None else DEFAULT_OUTPUT_FIELDS)
        if item.strip()
    ]
    output_fields = [
        name
        for name in dict.fromkeys(requested_output_fields)
        if name in fields_by_name and name != vector_field
    ]
    if "metadata" in fields_by_name and "metadata" not in output_fields:
        output_fields.append("metadata")
    if "project_tag" in fields_by_name and "project_tag" not in output_fields:
        output_fields.append("project_tag")

    metric_type = ""
    index_type = ""
    for index in list(getattr(collection, "indexes", None) or []):
        if str(getattr(index, "field_name", "") or "") != vector_field:
            continue
        params = getattr(index, "params", None)
        if not isinstance(params, dict):
            params = {}
        metric_type = str(params.get("metric_type") or "").strip().upper()
        index_type = str(params.get("index_type") or "").strip().upper()
        break
    if not metric_type:
        metric_type = str(os.getenv("SIQ_MILVUS_METRIC_TYPE") or DEFAULT_METRIC_TYPE).strip().upper()

    search_params: dict[str, Any] = {}
    if index_type == "HNSW":
        search_params["ef"] = 128
    return {
        "vector_field": vector_field,
        "output_fields": output_fields,
        "metric_type": metric_type,
        "search_params": search_params,
    }


def _search_milvus_collection(
    collection_name: str,
    embedding: list[float],
    *,
    top_k: int,
    expr: str | None = None,
) -> list[dict[str, Any]]:
    from pymilvus import Collection, connections, utility  # type: ignore[import-not-found]

    host = os.getenv("SIQ_MILVUS_HOST") or os.getenv("MILVUS_HOST") or "127.0.0.1"
    port = os.getenv("SIQ_MILVUS_PORT") or os.getenv("MILVUS_PORT") or "19530"
    alias = os.getenv("SIQ_MILVUS_ALIAS") or "siq_deal_retrieval"
    connections.connect(alias=alias, host=host, port=port)
    physical_name = _physical_collection(collection_name)
    if not utility.has_collection(physical_name, using=alias):
        return []
    collection = Collection(physical_name, using=alias)
    collection.load()
    search_config = _collection_search_config(collection)
    search_kwargs: dict[str, Any] = {
        "data": [embedding],
        "anns_field": search_config["vector_field"],
        "param": {
            "metric_type": search_config["metric_type"],
            "params": search_config["search_params"],
        },
        "limit": top_k,
        "output_fields": search_config["output_fields"],
    }
    if expr:
        search_kwargs["expr"] = expr
    results = collection.search(
        **search_kwargs,
    )
    hits: list[dict[str, Any]] = []
    first = results[0] if results else []
    for index, hit in enumerate(first):
        hits.append(_normalize_hit(collection_name, hit, index))
    return hits


def retrieve_vector_hits(
    *,
    query: str,
    profile_id: str,
    private_query: str | None = None,
    enabled: bool | None = None,
    collections: list[str] | tuple[str, ...] | None = None,
    top_k: int | str | None = 10,
    timeout: float = 10.0,
) -> dict[str, Any]:
    should_run = _env_bool("SIQ_VECTOR_RETRIEVAL_ENABLED") if enabled is None else bool(enabled)
    collection_names = _collections(profile_id, collections)
    physical_collections = {
        collection_name: _physical_collection(collection_name)
        for collection_name in collection_names
    }
    embedding_configured = bool(_embedding_endpoint())
    if not should_run:
        return {
            "schema_version": VECTOR_RETRIEVAL_SCHEMA,
            "enabled": False,
            "configured": embedding_configured,
            "status": "skipped",
            "reason": "vector_retrieval_disabled",
            "milvus_used": False,
            "collections": collection_names,
            "physical_collections": physical_collections,
            "hits": [],
            "hit_count": 0,
            "methodology_hits": [],
            "methodology_hit_count": 0,
        }
    if not embedding_configured:
        return {
            "schema_version": VECTOR_RETRIEVAL_SCHEMA,
            "enabled": True,
            "configured": False,
            "status": "skipped",
            "reason": "embedding_endpoint_not_configured",
            "milvus_used": False,
            "collections": collection_names,
            "physical_collections": physical_collections,
            "hits": [],
            "hit_count": 0,
            "methodology_hits": [],
            "methodology_hit_count": 0,
        }
    if find_spec("pymilvus") is None:
        return {
            "schema_version": VECTOR_RETRIEVAL_SCHEMA,
            "enabled": True,
            "configured": True,
            "status": "error",
            "reason": "pymilvus_not_installed",
            "milvus_used": False,
            "collections": collection_names,
            "physical_collections": physical_collections,
            "hits": [],
            "hit_count": 0,
            "methodology_hits": [],
            "methodology_hit_count": 0,
        }
    limit = _normalize_top_k(top_k)
    failed_collection: str | None = None
    failure_stage = "embedding"
    try:
        embedding = _embed_query(str(query or "")[:600], timeout=timeout)
        normalized_private_query = str(private_query or "").strip()[:600]
        private_embedding = (
            _embed_query(normalized_private_query, timeout=timeout)
            if normalized_private_query and normalized_private_query != str(query or "")[:600]
            else embedding
        )
        hits_by_collection: dict[str, list[dict[str, Any]]] = {}
        failure_stage = "collection_search"
        for collection_name in collection_names:
            failed_collection = collection_name
            hits_by_collection[collection_name] = _search_milvus_collection(
                collection_name,
                private_embedding if collection_name == profile_id else embedding,
                top_k=limit,
            )
        methodology_hits: list[dict[str, Any]] = []
        project_tag = _managed_knowledge_project_tag()
        if profile_id in collection_names:
            failed_collection = profile_id
            failure_stage = "methodology_search"
            filtered_hits = _search_milvus_collection(
                profile_id,
                private_embedding,
                top_k=min(limit, 10),
                expr=f'project_tag == "{project_tag}"',
            )
            methodology_hits = [
                {**item, "knowledge_lane": "methodology"}
                for item in filtered_hits
                if _is_managed_methodology_hit(
                    item,
                    profile_id=profile_id,
                    project_tag=project_tag,
                )
            ]
        failed_collection = None
    except Exception as exc:  # Milvus raises several backend-specific exception classes.
        return {
            "schema_version": VECTOR_RETRIEVAL_SCHEMA,
            "enabled": True,
            "configured": True,
            "status": "error",
            "reason": "vector_retrieval_failed",
            "milvus_used": False,
            "error": str(exc)[:300],
            "error_type": type(exc).__name__,
            "failure_stage": failure_stage,
            "failed_collection": failed_collection,
            "failed_physical_collection": physical_collections.get(failed_collection or ""),
            "collections": collection_names,
            "physical_collections": physical_collections,
            "hits": [],
            "hit_count": 0,
            "methodology_hits": [],
            "methodology_hit_count": 0,
        }
    hits = _round_robin_hits(hits_by_collection, limit=limit)
    return {
        "schema_version": VECTOR_RETRIEVAL_SCHEMA,
        "enabled": True,
        "configured": True,
        "status": "completed",
        "reason": None,
        "milvus_used": True,
        "collections": collection_names,
        "physical_collections": physical_collections,
        "collection_hit_counts": {
            collection_name: len(items)
            for collection_name, items in hits_by_collection.items()
        },
        "hits": hits,
        "hit_count": len(hits),
        "methodology_project_tag": project_tag,
        "methodology_hits": methodology_hits,
        "methodology_hit_count": len(methodology_hits),
    }
