from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import text

from services import agent_memory_milvus, market_document_identity, rerank_provider

PROFILE_ALIASES = {
    "assistant": "siq_assistant",
    "analysis": "siq_analysis",
    "factchecker": "siq_factchecker",
    "tracking": "siq_tracking",
    "legal": "siq_legal",
}

PRIMARY_MARKET_PROFILE_PREFIX = "siq_ic_"
DEFAULT_TENANT_ID = os.getenv("SIQ_DEFAULT_TENANT_ID", "default")
SESSION_ID_RE = re.compile(
    r"^user-(?P<user_id>\d+)-(?P<profile>.+?)-(?P<uuid>(?:"
    r"[0-9a-fA-F]{8}|[0-9a-fA-F]{32}|"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    r"))$"
)
FULL_RECALL_MARKERS = (
    "全量检索",
    "全量记忆",
    "完整历史",
    "所有历史",
    "所有记忆",
    "所有内容",
    "全部记忆",
    "全部内容",
    "不要遗忘",
    "不考虑时间",
    "full recall",
    "all memories",
    "all history",
)
RESEARCH_IDENTITY_FIELDS = ("market", "company_id", "filing_id", "parse_run_id")


@dataclass(frozen=True)
class MemoryRequestContext:
    tenant_id: str
    user_id: int | None
    profile: str
    agent_group: str
    session_id: str
    deal_id: str | None = None
    project_id: str | None = None
    visibility: str | None = None
    market: str | None = None
    company_id: str | None = None
    filing_id: str | None = None
    parse_run_id: str | None = None


def normalize_research_identity(value: Mapping[str, Any] | None) -> dict[str, str]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    output: dict[str, str] = {}
    for field in RESEARCH_IDENTITY_FIELDS:
        text_value = str(raw.get(field) or "").strip()
        if text_value:
            output[field] = text_value
    if output.get("market"):
        output["market"] = market_document_identity.normalize_market_code(output["market"])
    return output


def context_research_identity(context: MemoryRequestContext) -> dict[str, str]:
    return normalize_research_identity(
        {
            "market": context.market,
            "company_id": context.company_id,
            "filing_id": context.filing_id,
            "parse_run_id": context.parse_run_id,
        }
    )


def complete_context_research_identity(context: MemoryRequestContext) -> dict[str, str] | None:
    identity = context_research_identity(context)
    if not identity:
        return None
    return identity if all(identity.get(field) for field in RESEARCH_IDENTITY_FIELDS) else None


def metadata_with_research_identity(
    context: MemoryRequestContext,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    output = dict(metadata) if isinstance(metadata, Mapping) else {}
    identity = context_research_identity(context)
    if identity:
        output["research_identity"] = identity
    return output


def _bool_env(name: str, default: str = "auto") -> str:
    return os.getenv(name, default).strip().lower()


def _schema_name() -> str:
    schema = os.getenv("SIQ_AGENT_MEMORY_SCHEMA", "agent_memory").strip() or "agent_memory"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError(f"Invalid SIQ_AGENT_MEMORY_SCHEMA: {schema!r}")
    return schema


def _table(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise RuntimeError(f"Invalid agent memory table name: {name!r}")
    return f"{_schema_name()}.{name}"


def normalize_profile(profile: str | None) -> str:
    raw_profile = (profile or "siq_assistant").strip()
    return PROFILE_ALIASES.get(raw_profile, raw_profile)


def infer_agent_group(profile: str | None) -> str:
    normalized = normalize_profile(profile)
    if normalized.startswith(PRIMARY_MARKET_PROFILE_PREFIX):
        return "primary_market"
    return "secondary_market"


def default_visibility_for_context(agent_group: str, deal_id: str | None = None, project_id: str | None = None) -> str:
    if agent_group == "primary_market" and (deal_id or project_id):
        return os.getenv("SIQ_AGENT_MEMORY_PRIMARY_MARKET_VISIBILITY", "project_shared")
    return os.getenv("SIQ_AGENT_MEMORY_DEFAULT_VISIBILITY", "user_private")


def context_from_session_id(
    session_id: str,
    *,
    profile: str | None = None,
    user_id: int | None = None,
    tenant_id: str | None = None,
    deal_id: str | None = None,
    project_id: str | None = None,
    visibility: str | None = None,
    research_identity: Mapping[str, Any] | None = None,
) -> MemoryRequestContext | None:
    resolved_user_id = user_id
    resolved_profile = normalize_profile(profile)
    match = SESSION_ID_RE.match(session_id or "")
    if match:
        if resolved_user_id is None:
            resolved_user_id = int(match.group("user_id"))
        if profile is None:
            resolved_profile = normalize_profile(match.group("profile"))

    if not session_id or not resolved_profile:
        return None

    agent_group = infer_agent_group(resolved_profile)
    if agent_group == "primary_market" and not (deal_id or project_id):
        return None
    resolved_visibility = visibility or default_visibility_for_context(agent_group, deal_id, project_id)
    identity = normalize_research_identity(research_identity)
    return MemoryRequestContext(
        tenant_id=tenant_id or DEFAULT_TENANT_ID,
        user_id=resolved_user_id,
        profile=resolved_profile,
        agent_group=agent_group,
        session_id=session_id,
        deal_id=deal_id,
        project_id=project_id,
        visibility=resolved_visibility,
        market=identity.get("market"),
        company_id=identity.get("company_id"),
        filing_id=identity.get("filing_id"),
        parse_run_id=identity.get("parse_run_id"),
    )


def _session_dialect_name(async_session: Any) -> str:
    bind = getattr(async_session, "bind", None) or getattr(async_session, "get_bind", lambda: None)()
    dialect = getattr(bind, "dialect", None)
    return getattr(dialect, "name", "")


def memory_enabled(async_session: Any | None = None) -> bool:
    global_enabled = _bool_env("SIQ_AGENT_MEMORY_ENABLED")
    if global_enabled in {"0", "false", "no", "off"}:
        return False
    if async_session is not None and _session_dialect_name(async_session) != "postgresql":
        return os.getenv("SIQ_AGENT_MEMORY_ALLOW_SQLITE", "0").strip() == "1"
    return True


def memory_write_enabled(async_session: Any | None = None) -> bool:
    write_enabled = _bool_env("SIQ_AGENT_MEMORY_WRITE_ENABLED")
    if write_enabled in {"0", "false", "no", "off"}:
        return False
    return memory_enabled(async_session)


def memory_retrieval_enabled(async_session: Any | None = None) -> bool:
    retrieval_enabled = _bool_env("SIQ_AGENT_MEMORY_RETRIEVAL_ENABLED")
    if retrieval_enabled in {"0", "false", "no", "off"}:
        return False
    return memory_enabled(async_session)


def pgvector_enabled(async_session: Any | None = None) -> bool:
    if agent_memory_milvus.vector_backend() != "pgvector":
        return False
    vector_enabled = _bool_env("SIQ_AGENT_MEMORY_PGVECTOR_ENABLED")
    if vector_enabled in {"0", "false", "no", "off"}:
        return False
    return memory_retrieval_enabled(async_session)


def milvus_enabled(async_session: Any | None = None) -> bool:
    return agent_memory_milvus.milvus_enabled() and memory_retrieval_enabled(async_session)


def memory_extraction_enabled(async_session: Any | None = None) -> bool:
    extraction_enabled = _bool_env("SIQ_AGENT_MEMORY_EXTRACTION_ENABLED")
    if extraction_enabled in {"0", "false", "no", "off"}:
        return False
    return memory_write_enabled(async_session)


def _embedding_endpoint() -> str:
    base = str(
        os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL")
        or os.getenv("SIQ_EMBEDDING_BASE_URL")
        or os.getenv("EMBEDDING_BASE_URL")
        or ""
    ).strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/v1"):
        return base + "/embeddings"
    if base.endswith("/v1/embeddings"):
        return base
    return base + "/v1/embeddings"


def _embedding_model() -> str:
    return (
        os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_MODEL")
        or os.getenv("SIQ_EMBEDDING_MODEL")
        or os.getenv("EMBEDDING_MODEL")
        or "Qwen3-VL-Embedding-2B"
    )


async def _embed_text(value: str, *, timeout: float = 10.0) -> list[float] | None:
    endpoint = _embedding_endpoint()
    if not endpoint:
        return None
    headers: dict[str, str] = {"Content-Type": "application/json"}
    api_key = os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_API_KEY") or os.getenv("SIQ_EMBEDDING_API_KEY") or os.getenv("EMBEDDING_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            endpoint,
            headers=headers,
            json={"model": _embedding_model(), "input": value[:4000]},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        return None
    embedding = data[0].get("embedding") if isinstance(data[0], dict) else None
    if not isinstance(embedding, list):
        return None
    return [float(item) for item in embedding]


def _vector_literal(embedding: list[float]) -> str:
    return "[" + ",".join(f"{float(item):.8g}" for item in embedding) + "]"


def is_full_recall_query(query: str) -> bool:
    value = str(query or "").lower()
    return any(marker in value for marker in FULL_RECALL_MARKERS)


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def memory_recency_weight(updated_at: Any, *, source_type: str | None = None, query: str = "") -> float:
    if is_full_recall_query(query):
        return 1.0
    if str(source_type or "") == "profile_file":
        return 1.0
    enabled = _bool_env("SIQ_AGENT_MEMORY_TIME_DECAY_ENABLED", "true")
    if enabled in {"0", "false", "no", "off"}:
        return 1.0
    parsed = _coerce_datetime(updated_at)
    if parsed is None:
        return 1.0
    half_life_days = max(1.0, float(os.getenv("SIQ_AGENT_MEMORY_TIME_DECAY_HALF_LIFE_DAYS", "30")))
    floor = max(0.0, min(float(os.getenv("SIQ_AGENT_MEMORY_TIME_DECAY_FLOOR", "0.35")), 1.0))
    age_days = max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 86400)
    return max(floor, math.pow(0.5, age_days / half_life_days))


def apply_time_decay_to_candidates(candidates: list[dict[str, Any]], *, query: str) -> list[dict[str, Any]]:
    adjusted: list[dict[str, Any]] = []
    for item in candidates:
        updated_at = item.get("updated_at")
        source_type = str(item.get("source_type") or item.get("source_kind") or "")
        weight = memory_recency_weight(updated_at, source_type=source_type, query=query)
        base_score = item.get("rerank_score", item.get("score", 0.0))
        try:
            numeric_score = float(base_score if base_score is not None else 0.0)
        except (TypeError, ValueError):
            numeric_score = 0.0
        cloned = dict(item)
        cloned["recency_weight"] = round(weight, 4)
        cloned["final_score"] = numeric_score * weight
        adjusted.append(cloned)
    return sorted(
        adjusted,
        key=lambda item: (
            float(item.get("final_score") or 0.0),
            float(item.get("importance") or 0.0),
        ),
        reverse=True,
    )


def extract_explicit_memory_text(content: str) -> str | None:
    text_value = " ".join(str(content or "").split())
    if not text_value:
        return None
    patterns = [
        r"(?:请你记住|请记住|帮我记住|记住[:：]?|以后默认|后续默认|我的偏好是|我偏好|我希望你以后)(?P<memory>.+)",
        r"(?:remember that|please remember|my preference is)(?P<memory>.+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text_value, flags=re.IGNORECASE)
        if not match:
            continue
        memory_text = str(match.group("memory") or "").strip(" ：:，,。.")
        if 4 <= len(memory_text) <= 1200:
            return memory_text
    return None


def classify_explicit_memory_type(content: str, *, agent_group: str) -> str:
    value = str(content or "").lower()
    correction_markers = ("更正", "纠正", "你之前说错", "不是", "以后不要", "correction", "you were wrong")
    if any(marker in value for marker in correction_markers):
        return "correction"
    if agent_group == "primary_market":
        return "project_fact"
    return "user_preference"


def _json_param(value: Any) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


async def record_session(
    async_session: Any,
    context: MemoryRequestContext,
    *,
    title: str | None = None,
    metadata: dict[str, Any] | None = None,
    commit: bool = False,
) -> None:
    if not memory_write_enabled(async_session):
        return
    if context.user_id is None and (context.visibility or "user_private") == "user_private":
        return
    metadata = metadata_with_research_identity(context, metadata)

    statement = text(
        f"""
        INSERT INTO {_table("sessions")} (
            session_id,
            tenant_id,
            user_id,
            profile,
            agent_group,
            title,
            visibility,
            deal_id,
            project_id,
            metadata_json,
            updated_at,
            last_active_at
        )
        VALUES (
            :session_id,
            :tenant_id,
            :user_id,
            :profile,
            :agent_group,
            :title,
            :visibility,
            :deal_id,
            :project_id,
            CAST(:metadata_json AS jsonb),
            now(),
            now()
        )
        ON CONFLICT (session_id) DO UPDATE SET
            tenant_id = EXCLUDED.tenant_id,
            user_id = COALESCE(EXCLUDED.user_id, sessions.user_id),
            profile = EXCLUDED.profile,
            agent_group = EXCLUDED.agent_group,
            title = COALESCE(EXCLUDED.title, sessions.title),
            visibility = EXCLUDED.visibility,
            deal_id = COALESCE(EXCLUDED.deal_id, sessions.deal_id),
            project_id = COALESCE(EXCLUDED.project_id, sessions.project_id),
            metadata_json = sessions.metadata_json || EXCLUDED.metadata_json,
            updated_at = now(),
            last_active_at = now()
        """
    )
    await async_session.execute(
        statement,
        {
            "session_id": context.session_id,
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "profile": context.profile,
            "agent_group": context.agent_group,
            "title": title,
            "visibility": context.visibility or default_visibility_for_context(context.agent_group, context.deal_id, context.project_id),
            "deal_id": context.deal_id,
            "project_id": context.project_id,
            "metadata_json": _json_param(metadata),
        },
    )
    if commit:
        await async_session.commit()


async def record_message(
    async_session: Any,
    context: MemoryRequestContext,
    *,
    role: str,
    content: str,
    attachments: Any = None,
    model_name: str | None = None,
    token_count: int | None = None,
    created_at: datetime | None = None,
    commit: bool = False,
) -> int | None:
    if not memory_write_enabled(async_session):
        return None
    if context.user_id is None and (context.visibility or "user_private") == "user_private":
        return None

    research_identity = complete_context_research_identity(context)
    await record_session(async_session, context, commit=False)
    statement = text(
        f"""
        INSERT INTO {_table("messages")} (
            session_id,
            tenant_id,
            user_id,
            profile,
            agent_group,
            role,
            content,
            attachments_json,
            research_identity_json,
            token_count,
            model_name,
            created_at
        )
        VALUES (
            :session_id,
            :tenant_id,
            :user_id,
            :profile,
            :agent_group,
            :role,
            :content,
            CAST(:attachments_json AS jsonb),
            CAST(:research_identity_json AS jsonb),
            :token_count,
            :model_name,
            COALESCE(:created_at, now())
        )
        RETURNING id
        """
    )
    result = await async_session.execute(
        statement,
        {
            "session_id": context.session_id,
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "profile": context.profile,
            "agent_group": context.agent_group,
            "role": role,
            "content": content,
            "attachments_json": _json_param(attachments),
            "research_identity_json": (
                json.dumps(research_identity, ensure_ascii=False)
                if research_identity
                else None
            ),
            "token_count": token_count,
            "model_name": model_name,
            "created_at": created_at,
        },
    )
    row = result.first()
    if commit:
        await async_session.commit()
    if not row:
        return None
    return int(row[0])


async def record_project_access_binding(
    async_session: Any,
    *,
    tenant_id: str | None,
    resource_type: str,
    resource_id: str,
    principal_type: str,
    principal_id: str | int,
    role: str = "viewer",
    commit: bool = False,
) -> None:
    if not memory_write_enabled(async_session):
        return

    statement = text(
        f"""
        INSERT INTO {_table("access_bindings")} (
            tenant_id,
            resource_type,
            resource_id,
            principal_type,
            principal_id,
            role
        )
        VALUES (
            :tenant_id,
            :resource_type,
            :resource_id,
            :principal_type,
            :principal_id,
            :role
        )
        ON CONFLICT (tenant_id, resource_type, resource_id, principal_type, principal_id, role) DO NOTHING
        """
    )
    await async_session.execute(
        statement,
        {
            "tenant_id": tenant_id or DEFAULT_TENANT_ID,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "principal_type": principal_type,
            "principal_id": str(principal_id),
            "role": role,
        },
    )
    if commit:
        await async_session.commit()


async def promote_memory_item(
    async_session: Any,
    context: MemoryRequestContext,
    *,
    content: str,
    title: str | None = None,
    memory_type: str = "note",
    source_type: str | None = None,
    source_id: str | None = None,
    confidence: float = 0.7,
    importance: float = 0.5,
    metadata: dict[str, Any] | None = None,
    status: str = "active",
    commit: bool = False,
) -> int | None:
    if not memory_write_enabled(async_session):
        return None
    if context.user_id is None and (context.visibility or "user_private") == "user_private":
        return None
    normalized_content = " ".join(str(content or "").split())
    if not normalized_content:
        return None
    visibility = context.visibility or default_visibility_for_context(context.agent_group, context.deal_id, context.project_id)
    owner_user_id = context.user_id if visibility == "user_private" else None
    confidence_value = max(0.0, min(float(confidence), 1.0))
    importance_value = max(0.0, min(float(importance), 1.0))
    metadata = metadata_with_research_identity(context, metadata)
    metadata_json = _json_param(metadata)

    dedupe_result = await async_session.execute(
        text(
            f"""
            SELECT id
            FROM {_table("memory_items")}
            WHERE tenant_id = :tenant_id
              AND profile = :profile
              AND visibility = :visibility
              AND COALESCE(owner_user_id, -1) = COALESCE(:owner_user_id, -1)
              AND COALESCE(deal_id, '') = COALESCE(:deal_id, '')
              AND COALESCE(project_id, '') = COALESCE(:project_id, '')
              AND memory_type = :memory_type
              AND normalized_content = :normalized_content
              AND status = 'active'
              AND deleted_at IS NULL
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ),
        {
            "tenant_id": context.tenant_id,
            "profile": context.profile,
            "visibility": visibility,
            "owner_user_id": owner_user_id,
            "deal_id": context.deal_id,
            "project_id": context.project_id,
            "memory_type": memory_type,
            "normalized_content": normalized_content,
        },
    )
    duplicate = dedupe_result.first()
    if duplicate:
        memory_id = int(duplicate[0])
        await async_session.execute(
            text(
                f"""
                UPDATE {_table("memory_items")}
                SET confidence = GREATEST(confidence, :confidence),
                    importance = GREATEST(importance, :importance),
                    source_type = COALESCE(:source_type, source_type),
                    source_id = COALESCE(:source_id, source_id),
                    metadata_json = metadata_json || CAST(:metadata_json AS jsonb),
                    updated_at = now()
                WHERE id = :memory_id
                """
            ),
            {
                "memory_id": memory_id,
                "confidence": confidence_value,
                "importance": importance_value,
                "source_type": source_type,
                "source_id": source_id,
                "metadata_json": metadata_json,
            },
        )
    else:
        memory_id = 0

    if not memory_id:
        statement = text(
            f"""
            INSERT INTO {_table("memory_items")} (
                tenant_id,
                owner_user_id,
                created_by,
                profile,
                agent_group,
                visibility,
                deal_id,
                project_id,
                memory_type,
                title,
                content,
                normalized_content,
                source_type,
                source_id,
                confidence,
                importance,
                status,
                metadata_json
            )
            VALUES (
                :tenant_id,
                :owner_user_id,
                :created_by,
                :profile,
                :agent_group,
                :visibility,
                :deal_id,
                :project_id,
                :memory_type,
                :title,
                :content,
                :normalized_content,
                :source_type,
                :source_id,
                :confidence,
                :importance,
                :status,
                CAST(:metadata_json AS jsonb)
            )
            RETURNING id
            """
        )
        result = await async_session.execute(
            statement,
            {
                "tenant_id": context.tenant_id,
                "owner_user_id": owner_user_id,
                "created_by": context.user_id,
                "profile": context.profile,
                "agent_group": context.agent_group,
                "visibility": visibility,
                "deal_id": context.deal_id,
                "project_id": context.project_id,
                "memory_type": memory_type,
                "title": title,
                "content": content,
                "normalized_content": normalized_content,
                "source_type": source_type,
                "source_id": source_id,
                "confidence": confidence_value,
                "importance": importance_value,
                "status": status,
                "metadata_json": metadata_json,
            },
        )
        row = result.first()
        if not row:
            if commit:
                await async_session.commit()
            return None
        memory_id = int(row[0])
    embedding = None
    if milvus_enabled(async_session) or pgvector_enabled(async_session):
        try:
            embedding = await _embed_text(normalized_content)
        except (ValueError, httpx.HTTPError, RuntimeError):
            embedding = None
    if embedding and milvus_enabled(async_session):
        try:
            agent_memory_milvus.upsert_records(
                [
                    agent_memory_milvus.AgentMemoryVectorRecord(
                        id=f"memory_item:{memory_id}",
                        vector=embedding,
                        tenant_id=context.tenant_id,
                        visibility=visibility,
                        owner_user_id=str(context.user_id or ""),
                        profile=context.profile,
                        agent_group=context.agent_group,
                        deal_id=context.deal_id or "",
                        project_id=context.project_id or "",
                        memory_type=memory_type,
                        source_kind="memory_item",
                        source_id=str(memory_id),
                        content_hash=hashlib.sha256(normalized_content.encode("utf-8")).hexdigest(),
                        title=title or memory_type,
                        content=normalized_content,
                        metadata_json=metadata_json,
                        research_market=context.market or "",
                        research_company_id=context.company_id or "",
                        research_filing_id=context.filing_id or "",
                        research_parse_run_id=context.parse_run_id or "",
                        updated_at_ts=int(time.time()),
                    )
                ]
            )
        except Exception as exc:
            if os.getenv("SIQ_AGENT_MEMORY_STRICT", "0").strip() == "1":
                raise
            print(f"[agent-memory] failed to upsert memory item {memory_id} to Milvus: {exc}")
    elif embedding:
        await async_session.execute(
            text(
                f"""
                INSERT INTO {_table("memory_embeddings")} (
                    memory_id,
                    embedding_model,
                    embedding,
                    content_hash
                )
                VALUES (
                    :memory_id,
                    :embedding_model,
                    CAST(:embedding AS vector),
                    :content_hash
                )
                """
            ),
            {
                "memory_id": memory_id,
                "embedding_model": _embedding_model(),
                "embedding": _vector_literal(embedding),
                "content_hash": hashlib.sha256(normalized_content.encode("utf-8")).hexdigest(),
            },
        )
    if commit:
        await async_session.commit()
    return memory_id


async def maybe_promote_explicit_memory(
    async_session: Any,
    context: MemoryRequestContext,
    *,
    role: str,
    content: str,
    source_id: str | int | None = None,
    commit: bool = False,
) -> int | None:
    if role != "user" or not memory_extraction_enabled(async_session):
        return None
    memory_text = extract_explicit_memory_text(content)
    if not memory_text:
        return None
    return await promote_memory_item(
        async_session,
        context,
        title="用户显式记忆",
        content=memory_text,
        memory_type=classify_explicit_memory_type(content, agent_group=context.agent_group),
        source_type="chat_message",
        source_id=str(source_id) if source_id is not None else context.session_id,
        confidence=0.9,
        importance=0.75,
        metadata={"extraction": "explicit_memory_rule_v1"},
        status="active",
        commit=commit,
    )


def _memory_acl_sql(visibility_scope: str = "all") -> str:
    if visibility_scope == "user_private":
        return """
      AND mi.visibility = 'user_private'
      AND mi.owner_user_id = :user_id
      AND mi.profile = :profile
    """
    if visibility_scope != "all":
        raise ValueError(f"unsupported memory visibility scope: {visibility_scope}")
    return """
      AND (
        (
          mi.visibility = 'system_shared'
          AND mi.agent_group = :agent_group
          AND (
            mi.profile = :profile
            OR mi.profile = :system_shared_profile
          )
          OR (
            :agent_group = 'secondary_market'
            AND mi.visibility = 'system_shared'
            AND mi.agent_group = 'shared'
            AND mi.profile = 'shared'
          )
        )
        OR (
          mi.visibility = 'user_private'
          AND mi.owner_user_id = :user_id
          AND mi.profile = :profile
        )
        OR (
          mi.visibility = 'project_shared'
          AND mi.agent_group = :agent_group
          AND (
            (
              CAST(:deal_id AS TEXT) IS NOT NULL
              AND CAST(:project_id AS TEXT) IS NULL
              AND mi.deal_id = CAST(:deal_id AS TEXT)
            )
            OR (
              CAST(:project_id AS TEXT) IS NOT NULL
              AND CAST(:deal_id AS TEXT) IS NULL
              AND mi.project_id = CAST(:project_id AS TEXT)
            )
            OR (
              CAST(:deal_id AS TEXT) IS NOT NULL
              AND CAST(:project_id AS TEXT) IS NOT NULL
              AND mi.deal_id = CAST(:deal_id AS TEXT)
              AND mi.project_id = CAST(:project_id AS TEXT)
            )
          )
        )
      )
    """


def _memory_identity_sql(identity: Mapping[str, str] | None) -> str:
    if not identity:
        return """
      AND COALESCE(mi.metadata_json->'research_identity'->>'market', '') = ''
      AND COALESCE(mi.metadata_json->'research_identity'->>'company_id', '') = ''
      AND COALESCE(mi.metadata_json->'research_identity'->>'filing_id', '') = ''
      AND COALESCE(mi.metadata_json->'research_identity'->>'parse_run_id', '') = ''
    """
    if not all(identity.get(field) for field in RESEARCH_IDENTITY_FIELDS):
        raise ValueError("complete ResearchIdentity is required for PostgreSQL memory filtering")
    return """
      AND mi.metadata_json->'research_identity'->>'market' = :research_market
      AND mi.metadata_json->'research_identity'->>'company_id' = :research_company_id
      AND mi.metadata_json->'research_identity'->>'filing_id' = :research_filing_id
      AND mi.metadata_json->'research_identity'->>'parse_run_id' = :research_parse_run_id
    """


async def search_memory_items(
    async_session: Any,
    context: MemoryRequestContext,
    *,
    query: str,
    limit: int | None = None,
    min_score: float | None = None,
    visibility_scope: str = "all",
) -> list[dict[str, Any]]:
    if not memory_retrieval_enabled(async_session):
        return []
    if context.user_id is None:
        return []
    query_text = " ".join(str(query or "").split())
    if not query_text:
        return []
    if visibility_scope not in {"all", "user_private"}:
        raise ValueError(f"unsupported memory visibility scope: {visibility_scope}")
    raw_identity = context_research_identity(context)
    research_identity = complete_context_research_identity(context)
    if raw_identity and research_identity is None:
        return []
    identity_sql = _memory_identity_sql(research_identity)
    full_recall = is_full_recall_query(query_text)
    default_limit = os.getenv(
        "SIQ_AGENT_MEMORY_FULL_RECALL_MAX_ITEMS" if full_recall else "SIQ_AGENT_MEMORY_MAX_ITEMS",
        "25" if full_recall else "8",
    )
    item_limit = max(1, min(int(limit or default_limit), 50 if full_recall else 25))
    threshold = float(min_score if min_score is not None else os.getenv("SIQ_AGENT_MEMORY_MIN_SCORE", "0.72"))
    params = {
        "tenant_id": context.tenant_id,
        "user_id": context.user_id,
        "profile": context.profile,
        "agent_group": context.agent_group,
        "system_shared_profile": (
            "siq_ic_shared"
            if context.agent_group == "primary_market"
            else "shared"
        ),
        "deal_id": context.deal_id,
        "project_id": context.project_id,
        "limit": item_limit,
        "query_like": f"%{query_text[:300]}%",
    }
    if research_identity:
        params.update({f"research_{field}": value for field, value in research_identity.items()})

    embedding = None
    if milvus_enabled(async_session) or pgvector_enabled(async_session):
        try:
            embedding = await _embed_text(query_text)
        except (ValueError, httpx.HTTPError, RuntimeError):
            embedding = None

    candidates: list[dict[str, Any]] = []
    if embedding and milvus_enabled(async_session):
        try:
            expr = agent_memory_milvus.acl_expr(
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                deal_id=context.deal_id,
                project_id=context.project_id,
                profile=context.profile,
                agent_group=context.agent_group,
                research_identity=research_identity,
                visibility_scope=visibility_scope,
            )
            milvus_hits = await asyncio.to_thread(
                agent_memory_milvus.search_records,
                vector=embedding,
                expr=expr,
                limit=item_limit * 3,
            )
        except Exception as exc:
            milvus_hits = []
            if os.getenv("SIQ_AGENT_MEMORY_STRICT", "0").strip() == "1":
                raise
            print(f"[agent-memory] failed to search Milvus memory collection: {exc}")
        for item in milvus_hits:
            candidates.append(
                {
                    "id": item.get("source_id") or item.get("id"),
                    "visibility": item.get("visibility"),
                    "memory_type": item.get("memory_type"),
                    "title": item.get("title"),
                    "content": item.get("content"),
                    "text": item.get("content"),
                    "source_type": item.get("source_kind"),
                    "source_id": item.get("source_id") or item.get("source_path"),
                    "confidence": 0.8,
                    "importance": 0.6,
                    "score": item.get("score"),
                    "retrieval_source": "milvus_dense",
                    "updated_at": (
                        datetime.fromtimestamp(int(item["updated_at_ts"]), timezone.utc)
                        if item.get("updated_at_ts") and item.get("source_kind") != "profile_file"
                        else None
                    ),
                }
            )

    if embedding and pgvector_enabled(async_session):
        params["embedding"] = _vector_literal(embedding)
        statement = text(
            f"""
            SELECT
                mi.id,
                mi.visibility,
                mi.memory_type,
                mi.title,
                mi.content,
                mi.source_type,
                mi.source_id,
                mi.confidence,
                mi.importance,
                1 - (me.embedding <=> CAST(:embedding AS vector)) AS score,
                mi.updated_at
            FROM {_table("memory_items")} mi
            JOIN {_table("memory_embeddings")} me ON me.memory_id = mi.id
            WHERE mi.tenant_id = :tenant_id
              AND mi.status = 'active'
              AND mi.deleted_at IS NULL
              AND (mi.valid_from IS NULL OR mi.valid_from <= now())
              AND (mi.valid_until IS NULL OR mi.valid_until > now())
              {_memory_acl_sql(visibility_scope)}
              {identity_sql}
              AND 1 - (me.embedding <=> CAST(:embedding AS vector)) >= :min_score
            ORDER BY me.embedding <=> CAST(:embedding AS vector), mi.importance DESC, mi.updated_at DESC
            LIMIT :limit
            """
        )
        params["min_score"] = threshold
        result = await async_session.execute(statement, params)
        for row in result.mappings().all():
            item = dict(row)
            item["text"] = item.get("content")
            item["retrieval_source"] = "pgvector_dense"
            candidates.append(item)

    statement = text(
        f"""
        SELECT
            mi.id,
            mi.visibility,
            mi.memory_type,
            mi.title,
            mi.content,
            mi.source_type,
            mi.source_id,
            mi.confidence,
            mi.importance,
            CASE
              WHEN mi.normalized_content ILIKE :query_like THEN 0.6
              WHEN mi.title ILIKE :query_like THEN 0.55
              ELSE 0.25
            END AS score,
            mi.updated_at
        FROM {_table("memory_items")} mi
        WHERE mi.tenant_id = :tenant_id
          AND mi.status = 'active'
          AND mi.deleted_at IS NULL
          AND (mi.valid_from IS NULL OR mi.valid_from <= now())
          AND (mi.valid_until IS NULL OR mi.valid_until > now())
          {_memory_acl_sql(visibility_scope)}
          {identity_sql}
          AND (mi.normalized_content ILIKE :query_like OR mi.title ILIKE :query_like)
        ORDER BY score DESC, mi.importance DESC, mi.updated_at DESC
        LIMIT :limit
        """
    )
    result = await async_session.execute(statement, params)
    for row in result.mappings().all():
        item = dict(row)
        item["text"] = item.get("content")
        item["retrieval_source"] = "postgres_lexical"
        candidates.append(item)

    deduped: dict[str, dict[str, Any]] = {}
    for item in candidates:
        key = str(item.get("source_type") or "") + ":" + str(item.get("source_id") or item.get("id") or "")
        previous = deduped.get(key)
        if previous is None or float(item.get("score") or 0.0) > float(previous.get("score") or 0.0):
            deduped[key] = item
    merged = sorted(
        deduped.values(),
        key=lambda item: (
            float(item.get("score") or 0.0),
            float(item.get("importance") or 0.0),
        ),
        reverse=True,
    )
    if not merged:
        return []

    rerank_enabled = _bool_env("SIQ_AGENT_MEMORY_RERANK_ENABLED", "true") in {"1", "true", "yes", "on", "auto"}
    reranked = await asyncio.to_thread(
        rerank_provider.rerank_candidates,
        query=query_text,
        candidates=merged[: min(len(merged), 50)],
        enabled=rerank_enabled,
        top_n=item_limit,
        timeout=float(os.getenv("SIQ_AGENT_MEMORY_RERANK_TIMEOUT", "10")),
    )
    final_candidates = apply_time_decay_to_candidates(
        list(reranked.get("results") or merged[:item_limit]),
        query=query_text,
    )
    return final_candidates[:item_limit]


def build_memory_context_block(items: list[dict[str, Any]]) -> str | None:
    visible_items = [item for item in items if str(item.get("content") or "").strip()]
    if not visible_items:
        return None
    lines = [
        "<memory-context>",
        "以下为已通过权限过滤的长期记忆。当前用户问题和可验证证据优先级高于记忆。",
    ]
    for index, item in enumerate(visible_items, start=1):
        title = str(item.get("title") or item.get("memory_type") or "memory")
        content = str(item.get("content") or "").strip()
        source_type = item.get("source_type") or "unknown"
        source_id = item.get("source_id") or item.get("id")
        score = item.get("score")
        score_text = f"{float(score):.3f}" if isinstance(score, (int, float)) else "n/a"
        lines.append(
            f"[M{index}] title={title}; visibility={item.get('visibility')}; "
            f"score={score_text}; source={source_type}:{source_id}\n{content}"
        )
    lines.append("</memory-context>")
    return "\n".join(lines)


async def build_memory_context(
    async_session: Any,
    context: MemoryRequestContext,
    *,
    query: str,
    limit: int | None = None,
    visibility_scope: str = "all",
) -> str | None:
    items = await search_memory_items(
        async_session,
        context,
        query=query,
        limit=limit,
        visibility_scope=visibility_scope,
    )
    return build_memory_context_block(items)


async def record_session_summary(
    async_session: Any,
    context: MemoryRequestContext,
    *,
    summary: str,
    last_message_id: int | None = None,
    message_count: int = 0,
    commit: bool = False,
) -> None:
    if not memory_write_enabled(async_session):
        return
    if context.user_id is None and (context.visibility or "user_private") == "user_private":
        return

    await record_session(async_session, context, commit=False)
    statement = text(
        f"""
        INSERT INTO {_table("session_summaries")} (
            session_id,
            tenant_id,
            user_id,
            profile,
            summary,
            last_message_id,
            message_count,
            updated_at
        )
        VALUES (
            :session_id,
            :tenant_id,
            :user_id,
            :profile,
            :summary,
            :last_message_id,
            :message_count,
            now()
        )
        ON CONFLICT (tenant_id, user_id, profile, session_id) DO UPDATE SET
            summary = EXCLUDED.summary,
            last_message_id = EXCLUDED.last_message_id,
            message_count = EXCLUDED.message_count,
            summary_version = session_summaries.summary_version + 1,
            updated_at = now()
        """
    )
    await async_session.execute(
        statement,
        {
            "session_id": context.session_id,
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "profile": context.profile,
            "summary": summary,
            "last_message_id": last_message_id,
            "message_count": message_count,
        },
    )
    if commit:
        await async_session.commit()


async def record_feedback_event(
    async_session: Any,
    *,
    tenant_id: str | None,
    user_id: int | None,
    memory_id: int | None,
    session_id: str | None,
    feedback_type: str,
    feedback_text: str | None = None,
    commit: bool = False,
) -> None:
    if not memory_write_enabled(async_session):
        return
    if not str(feedback_type or "").strip():
        return
    await async_session.execute(
        text(
            f"""
            INSERT INTO {_table("feedback_events")} (
                tenant_id,
                user_id,
                memory_id,
                session_id,
                feedback_type,
                feedback_text
            )
            VALUES (
                :tenant_id,
                :user_id,
                :memory_id,
                :session_id,
                :feedback_type,
                :feedback_text
            )
            """
        ),
        {
            "tenant_id": tenant_id or DEFAULT_TENANT_ID,
            "user_id": user_id,
            "memory_id": memory_id,
            "session_id": session_id,
            "feedback_type": str(feedback_type).strip()[:80],
            "feedback_text": feedback_text,
        },
    )
    if commit:
        await async_session.commit()


async def user_has_project_memory_access(
    async_session: Any,
    *,
    tenant_id: str | None,
    user_id: int,
    deal_id: str | None = None,
    project_id: str | None = None,
) -> bool:
    if not memory_retrieval_enabled(async_session):
        return False
    resource_type = "deal" if deal_id else "project"
    resource_id = deal_id or project_id
    if not resource_id:
        return False

    statement = text(
        f"""
        SELECT 1
        FROM {_table("access_bindings")}
        WHERE tenant_id = :tenant_id
          AND resource_type = :resource_type
          AND resource_id = :resource_id
          AND principal_type = 'user'
          AND principal_id = :principal_id
        LIMIT 1
        """
    )
    result = await async_session.execute(
        statement,
        {
            "tenant_id": tenant_id or DEFAULT_TENANT_ID,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "principal_id": str(user_id),
        },
    )
    return result.first() is not None


__all__ = [
    "MemoryRequestContext",
    "build_memory_context",
    "build_memory_context_block",
    "classify_explicit_memory_type",
    "context_from_session_id",
    "default_visibility_for_context",
    "infer_agent_group",
    "memory_enabled",
    "memory_extraction_enabled",
    "memory_retrieval_enabled",
    "memory_write_enabled",
    "milvus_enabled",
    "normalize_profile",
    "extract_explicit_memory_text",
    "maybe_promote_explicit_memory",
    "promote_memory_item",
    "record_message",
    "record_feedback_event",
    "record_project_access_binding",
    "record_session",
    "record_session_summary",
    "user_has_project_memory_access",
]
