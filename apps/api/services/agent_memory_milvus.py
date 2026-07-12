from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

COLLECTION_SCHEMA_VERSION = "siq_agent_memory_milvus_v2"
DEFAULT_COLLECTION = "siq_agent_memory"
DEFAULT_VECTOR_DIM = 1024
VECTOR_FIELD = "vector"
RESEARCH_IDENTITY_FIELDS = (
    "research_market",
    "research_company_id",
    "research_filing_id",
    "research_parse_run_id",
)
OUTPUT_FIELDS = [
    "id",
    "tenant_id",
    "visibility",
    "owner_user_id",
    "profile",
    "agent_group",
    "deal_id",
    "project_id",
    "memory_type",
    "source_kind",
    "source_id",
    "source_path",
    "title",
    "content",
    "metadata_json",
    *RESEARCH_IDENTITY_FIELDS,
    "updated_at_ts",
]
REQUIRED_FIELDS = set(OUTPUT_FIELDS + [VECTOR_FIELD, "content_hash"])


@dataclass(frozen=True)
class AgentMemoryVectorRecord:
    id: str
    vector: list[float]
    tenant_id: str = "default"
    visibility: str = "user_private"
    owner_user_id: str = ""
    profile: str = ""
    agent_group: str = "secondary_market"
    deal_id: str = ""
    project_id: str = ""
    memory_type: str = "note"
    source_kind: str = "memory_item"
    source_id: str = ""
    source_path: str = ""
    content_hash: str = ""
    title: str = ""
    content: str = ""
    metadata_json: str = "{}"
    research_market: str = ""
    research_company_id: str = ""
    research_filing_id: str = ""
    research_parse_run_id: str = ""
    updated_at_ts: int = 0


def vector_backend() -> str:
    return os.getenv("SIQ_AGENT_MEMORY_VECTOR_BACKEND", "milvus").strip().lower()


def milvus_enabled() -> bool:
    return vector_backend() == "milvus"


def collection_name() -> str:
    return os.getenv("SIQ_AGENT_MEMORY_MILVUS_COLLECTION", DEFAULT_COLLECTION).strip() or DEFAULT_COLLECTION


def vector_dim() -> int:
    raw = os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_DIM", str(DEFAULT_VECTOR_DIM))
    try:
        value = int(raw)
    except ValueError:
        value = DEFAULT_VECTOR_DIM
    return max(1, min(value, 16384))


def _uri() -> str:
    host = os.getenv("SIQ_MILVUS_HOST") or os.getenv("MILVUS_HOST") or "127.0.0.1"
    port = os.getenv("SIQ_MILVUS_PORT") or os.getenv("MILVUS_PORT") or "19530"
    if str(host).startswith("http://") or str(host).startswith("https://"):
        return str(host)
    return f"http://{host}:{port}"


def _client() -> Any:
    from pymilvus import MilvusClient  # type: ignore[import-not-found]

    return MilvusClient(
        uri=_uri(),
        user=os.getenv("SIQ_MILVUS_USER") or os.getenv("MILVUS_USER") or "",
        password=os.getenv("SIQ_MILVUS_PASSWORD") or os.getenv("MILVUS_PASSWORD") or "",
        token=os.getenv("SIQ_MILVUS_TOKEN") or os.getenv("MILVUS_TOKEN") or "",
        db_name=os.getenv("SIQ_MILVUS_DB_NAME") or os.getenv("MILVUS_DB_NAME") or "",
    )


def _schema_field_names(client: Any, name: str) -> set[str]:
    description = client.describe_collection(name)
    fields = description.get("fields") if isinstance(description, dict) else []
    return {str(field.get("name")) for field in fields if isinstance(field, dict)}


def _recreate_on_schema_mismatch() -> bool:
    recreate = os.getenv("SIQ_AGENT_MEMORY_MILVUS_RECREATE_ON_SCHEMA_MISMATCH", "false").strip().lower()
    allow_destructive = os.getenv("SIQ_AGENT_MEMORY_MILVUS_ALLOW_DESTRUCTIVE_SCHEMA_RECREATE", "false").strip().lower()
    return recreate in {"1", "true", "yes", "on"} and allow_destructive in {"1", "true", "yes", "on"}


def collection_schema_preflight(*, client: Any | None = None, name: str | None = None) -> dict[str, Any]:
    resolved_client = client or _client()
    resolved_name = name or collection_name()
    exists = bool(resolved_client.has_collection(resolved_name))
    fields = _schema_field_names(resolved_client, resolved_name) if exists else set()
    missing = sorted(REQUIRED_FIELDS - fields) if exists else []
    return {
        "schema_version": COLLECTION_SCHEMA_VERSION,
        "collection_name": resolved_name,
        "exists": exists,
        "compatible": exists and not missing,
        "missing_fields": missing,
        "migration_required": bool(missing),
        "migration_action": (
            "create_versioned_collection_and_reindex"
            if missing
            else ("none" if exists else "create_collection")
        ),
        "destructive_recreate_enabled": _recreate_on_schema_mismatch(),
    }


def create_versioned_collection(
    *,
    client: Any,
    name: str,
    dimension: int | None = None,
    index_type: str | None = None,
    metric_type: str | None = None,
    require_absent: bool = True,
) -> None:
    from pymilvus import DataType, MilvusClient  # type: ignore[import-not-found]

    if require_absent and client.has_collection(name):
        raise RuntimeError(f"refusing to create versioned collection because it already exists: {name}")
    resolved_dimension = dimension if dimension is not None else vector_dim()
    if not 1 <= int(resolved_dimension) <= 16384:
        raise ValueError("Milvus vector dimension must be between 1 and 16384")

    schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
    schema.add_field(field_name="id", datatype=DataType.VARCHAR, max_length=128, is_primary=True)
    schema.add_field(field_name=VECTOR_FIELD, datatype=DataType.FLOAT_VECTOR, dim=int(resolved_dimension))
    schema.add_field(field_name="tenant_id", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="visibility", datatype=DataType.VARCHAR, max_length=32)
    schema.add_field(field_name="owner_user_id", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="profile", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="agent_group", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="deal_id", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="project_id", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="memory_type", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="source_kind", datatype=DataType.VARCHAR, max_length=64)
    schema.add_field(field_name="source_id", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="source_path", datatype=DataType.VARCHAR, max_length=1024)
    schema.add_field(field_name="content_hash", datatype=DataType.VARCHAR, max_length=128)
    schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="content", datatype=DataType.VARCHAR, max_length=8192)
    schema.add_field(field_name="metadata_json", datatype=DataType.VARCHAR, max_length=4096)
    schema.add_field(field_name="research_market", datatype=DataType.VARCHAR, max_length=16)
    schema.add_field(field_name="research_company_id", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="research_filing_id", datatype=DataType.VARCHAR, max_length=512)
    schema.add_field(field_name="research_parse_run_id", datatype=DataType.VARCHAR, max_length=256)
    schema.add_field(field_name="updated_at_ts", datatype=DataType.INT64)
    index_params = MilvusClient.prepare_index_params()
    index_params.add_index(
        field_name=VECTOR_FIELD,
        index_type=index_type or os.getenv("SIQ_AGENT_MEMORY_MILVUS_INDEX_TYPE", "HNSW"),
        metric_type=metric_type or os.getenv("SIQ_AGENT_MEMORY_MILVUS_METRIC_TYPE", "COSINE"),
        params={"M": 16, "efConstruction": 128},
    )
    client.create_collection(
        collection_name=name,
        schema=schema,
        index_params=index_params,
        timeout=float(os.getenv("SIQ_AGENT_MEMORY_MILVUS_TIMEOUT", "30")),
    )
    client.load_collection(name)


def ensure_collection() -> Any:
    client = _client()
    name = collection_name()
    preflight = collection_schema_preflight(client=client, name=name)
    recreated_after_drop = False
    if preflight["exists"]:
        missing = set(preflight["missing_fields"])
        if missing:
            if not _recreate_on_schema_mismatch():
                raise RuntimeError(
                    f"Milvus collection {name} schema is missing fields: {sorted(missing)}; "
                    "refusing to drop an existing collection. Create a versioned replacement collection and switch aliases, "
                    "or set both SIQ_AGENT_MEMORY_MILVUS_RECREATE_ON_SCHEMA_MISMATCH=true and "
                    "SIQ_AGENT_MEMORY_MILVUS_ALLOW_DESTRUCTIVE_SCHEMA_RECREATE=true only in a disposable environment."
                )
            client.drop_collection(name)
            recreated_after_drop = True
        else:
            client.load_collection(name)
            return client

    create_versioned_collection(client=client, name=name, require_absent=not recreated_after_drop)
    return client


def _clean_text(value: Any, *, max_length: int) -> str:
    return str(value or "")[:max_length]


def _record_payload(record: AgentMemoryVectorRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        VECTOR_FIELD: record.vector,
        "tenant_id": _clean_text(record.tenant_id, max_length=128),
        "visibility": _clean_text(record.visibility, max_length=32),
        "owner_user_id": _clean_text(record.owner_user_id, max_length=64),
        "profile": _clean_text(record.profile, max_length=128),
        "agent_group": _clean_text(record.agent_group, max_length=64),
        "deal_id": _clean_text(record.deal_id, max_length=256),
        "project_id": _clean_text(record.project_id, max_length=256),
        "memory_type": _clean_text(record.memory_type, max_length=64),
        "source_kind": _clean_text(record.source_kind, max_length=64),
        "source_id": _clean_text(record.source_id, max_length=256),
        "source_path": _clean_text(record.source_path, max_length=1024),
        "content_hash": _clean_text(record.content_hash, max_length=128),
        "title": _clean_text(record.title, max_length=512),
        "content": _clean_text(record.content, max_length=8192),
        "metadata_json": _clean_text(record.metadata_json, max_length=4096),
        "research_market": _clean_text(record.research_market, max_length=16),
        "research_company_id": _clean_text(record.research_company_id, max_length=256),
        "research_filing_id": _clean_text(record.research_filing_id, max_length=512),
        "research_parse_run_id": _clean_text(record.research_parse_run_id, max_length=256),
        "updated_at_ts": int(record.updated_at_ts or time.time()),
    }


def upsert_records(records: list[AgentMemoryVectorRecord], *, flush: bool = True) -> int:
    if not records or not milvus_enabled():
        return 0
    client = ensure_collection()
    name = collection_name()
    ids = [record.id for record in records]
    quoted_ids = ", ".join(json.dumps(item) for item in ids)
    try:
        client.delete(collection_name=name, filter=f"id in [{quoted_ids}]")
    except Exception:
        pass
    client.upsert(collection_name=name, data=[_record_payload(record) for record in records])
    if flush:
        client.flush(name)
    return len(records)


def flush_collection() -> None:
    if not milvus_enabled():
        return
    client = ensure_collection()
    client.flush(collection_name())


def _escape_expr(value: str) -> str:
    return json.dumps(str(value or ""))


def acl_expr(
    *,
    tenant_id: str,
    user_id: int | None,
    deal_id: str | None = None,
    project_id: str | None = None,
    profile: str | None = None,
    agent_group: str | None = None,
    research_identity: dict[str, str] | None = None,
) -> str:
    visibility_parts = ["visibility == \"system_shared\""]
    if user_id is not None:
        visibility_parts.append(f"(visibility == \"user_private\" and owner_user_id == {_escape_expr(str(user_id))})")
    project_parts: list[str] = []
    if deal_id:
        project_parts.append(f"deal_id == {_escape_expr(deal_id)}")
    if project_id:
        project_parts.append(f"project_id == {_escape_expr(project_id)}")
    if project_parts and agent_group:
        visibility_parts.append(
            f"(visibility == \"project_shared\" and agent_group == {_escape_expr(agent_group)} "
            f"and ({' or '.join(project_parts)}))"
        )
    expr = f"tenant_id == {_escape_expr(tenant_id)} and ({' or '.join(visibility_parts)})"
    if profile:
        expr += f" and (profile == {_escape_expr(profile)} or visibility != \"user_private\")"
    if research_identity:
        identity_fields = {
            "research_market": research_identity.get("market"),
            "research_company_id": research_identity.get("company_id"),
            "research_filing_id": research_identity.get("filing_id"),
            "research_parse_run_id": research_identity.get("parse_run_id"),
        }
        if not all(identity_fields.values()):
            raise ValueError("complete ResearchIdentity is required for Milvus filtering")
        for field, value in identity_fields.items():
            expr += f" and {field} == {_escape_expr(str(value))}"
    else:
        for field in RESEARCH_IDENTITY_FIELDS:
            expr += f' and {field} == ""'
    return expr


def search_records(
    *,
    vector: list[float],
    expr: str,
    limit: int,
) -> list[dict[str, Any]]:
    if not vector or not milvus_enabled():
        return []
    client = ensure_collection()
    results = client.search(
        collection_name=collection_name(),
        data=[vector],
        anns_field=VECTOR_FIELD,
        search_params={
            "metric_type": os.getenv("SIQ_AGENT_MEMORY_MILVUS_METRIC_TYPE", "COSINE"),
            "params": {"ef": int(os.getenv("SIQ_AGENT_MEMORY_MILVUS_EF", "64"))},
        },
        limit=max(1, min(limit, 50)),
        filter=expr,
        output_fields=OUTPUT_FIELDS,
    )
    hits: list[dict[str, Any]] = []
    first = results[0] if results else []
    for hit in first:
        entity = hit.get("entity") if isinstance(hit, dict) else {}
        payload = {field: entity.get(field) for field in OUTPUT_FIELDS} if isinstance(entity, dict) else {}
        payload["score"] = hit.get("distance") if isinstance(hit, dict) else None
        hits.append(payload)
    return hits


__all__ = [
    "AgentMemoryVectorRecord",
    "acl_expr",
    "collection_name",
    "collection_schema_preflight",
    "create_versioned_collection",
    "ensure_collection",
    "flush_collection",
    "milvus_enabled",
    "search_records",
    "upsert_records",
    "vector_backend",
    "vector_dim",
]
