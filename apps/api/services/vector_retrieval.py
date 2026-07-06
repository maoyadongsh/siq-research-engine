"""Optional Milvus vector retrieval adapter for SIQ Deal OS."""

from __future__ import annotations

import json
import os
from importlib.util import find_spec
from typing import Any

import httpx


VECTOR_RETRIEVAL_SCHEMA = "siq_vector_retrieval_result_v1"
MAX_VECTOR_TOP_K = 50
DEFAULT_VECTOR_FIELD = "embedding"
DEFAULT_TEXT_FIELDS = ("text", "content", "chunk", "quote", "claim")


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
                "model": os.getenv("SIQ_EMBEDDING_MODEL") or os.getenv("EMBEDDING_MODEL") or "text-embedding",
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


def _search_milvus_collection(collection_name: str, embedding: list[float], *, top_k: int) -> list[dict[str, Any]]:
    from pymilvus import Collection, connections, utility  # type: ignore[import-not-found]

    host = os.getenv("SIQ_MILVUS_HOST") or os.getenv("MILVUS_HOST") or "127.0.0.1"
    port = os.getenv("SIQ_MILVUS_PORT") or os.getenv("MILVUS_PORT") or "19530"
    alias = os.getenv("SIQ_MILVUS_ALIAS") or "siq_deal_retrieval"
    connections.connect(alias=alias, host=host, port=port)
    if not utility.has_collection(collection_name, using=alias):
        return []
    collection = Collection(collection_name, using=alias)
    collection.load()
    vector_field = os.getenv("SIQ_MILVUS_VECTOR_FIELD") or DEFAULT_VECTOR_FIELD
    output_fields = [
        item.strip()
        for item in (os.getenv("SIQ_MILVUS_OUTPUT_FIELDS") or "text,content,metadata,evidence_id,document_id").split(",")
        if item.strip()
    ]
    results = collection.search(
        data=[embedding],
        anns_field=vector_field,
        param={"metric_type": os.getenv("SIQ_MILVUS_METRIC_TYPE") or "COSINE", "params": {"ef": 128}},
        limit=top_k,
        output_fields=output_fields,
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
    enabled: bool | None = None,
    collections: list[str] | tuple[str, ...] | None = None,
    top_k: int | str | None = 10,
    timeout: float = 10.0,
) -> dict[str, Any]:
    should_run = _env_bool("SIQ_VECTOR_RETRIEVAL_ENABLED") if enabled is None else bool(enabled)
    collection_names = _collections(profile_id, collections)
    embedding_configured = bool(_embedding_endpoint())
    if not should_run:
        return {
            "schema_version": VECTOR_RETRIEVAL_SCHEMA,
            "enabled": False,
            "configured": embedding_configured,
            "status": "skipped",
            "reason": "vector_retrieval_disabled",
            "collections": collection_names,
            "hits": [],
            "hit_count": 0,
        }
    if not embedding_configured:
        return {
            "schema_version": VECTOR_RETRIEVAL_SCHEMA,
            "enabled": True,
            "configured": False,
            "status": "skipped",
            "reason": "embedding_endpoint_not_configured",
            "collections": collection_names,
            "hits": [],
            "hit_count": 0,
        }
    if find_spec("pymilvus") is None:
        return {
            "schema_version": VECTOR_RETRIEVAL_SCHEMA,
            "enabled": True,
            "configured": True,
            "status": "error",
            "reason": "pymilvus_not_installed",
            "collections": collection_names,
            "hits": [],
            "hit_count": 0,
        }
    limit = _normalize_top_k(top_k)
    try:
        embedding = _embed_query(str(query or "")[:600], timeout=timeout)
        hits: list[dict[str, Any]] = []
        for collection_name in collection_names:
            hits.extend(_search_milvus_collection(collection_name, embedding, top_k=limit))
    except (ImportError, OSError, ValueError, httpx.HTTPError, RuntimeError) as exc:
        return {
            "schema_version": VECTOR_RETRIEVAL_SCHEMA,
            "enabled": True,
            "configured": True,
            "status": "error",
            "reason": "vector_retrieval_failed",
            "error": str(exc)[:300],
            "collections": collection_names,
            "hits": [],
            "hit_count": 0,
        }
    hits = hits[:limit]
    return {
        "schema_version": VECTOR_RETRIEVAL_SCHEMA,
        "enabled": True,
        "configured": True,
        "status": "completed",
        "reason": None,
        "collections": collection_names,
        "hits": hits,
        "hit_count": len(hits),
    }
