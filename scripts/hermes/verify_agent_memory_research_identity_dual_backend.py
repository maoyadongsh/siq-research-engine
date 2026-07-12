#!/usr/bin/env python3
# isort: skip_file
"""Verify ResearchIdentity isolation through the real PostgreSQL and Milvus backends.

The runner creates a disposable PostgreSQL schema and Milvus collection, inserts the
same contract fixture into both stores, and invokes ``search_memory_items`` for a
small access matrix.  It is deliberately opt-in: no existing schema or collection
is touched, and a missing backend is reported as a failed verification rather than
silently skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = PROJECT_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import agent_memory_milvus, agent_memory_service  # noqa: E402


REPORT_SCHEMA = "siq_agent_memory_dual_backend_verification_v1"
DEFAULT_OUTPUT = PROJECT_ROOT / "artifacts/eval-runs/local/agent-memory-dual-backend-verification.json"
DIMENSION = 4
IDENTITY_A = {
    "market": "HK",
    "company_id": "HK:00700",
    "filing_id": "HK:00700:2025-annual",
    "parse_run_id": "parse-hk-00700",
}
IDENTITY_B = {
    "market": "HK",
    "company_id": "HK:09988",
    "filing_id": "HK:09988:2025-annual",
    "parse_run_id": "parse-hk-09988",
}


def _sync_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1).replace(
        "postgresql://", "postgresql+psycopg://", 1
    )


def _async_url(value: str) -> str:
    if "+asyncpg" in value:
        return value
    return value.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


def _ident(value: str) -> str:
    if not value.replace("_", "").isalnum() or not value[0].isalpha():
        raise ValueError(f"invalid generated identifier: {value!r}")
    return value


def _fixture() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cases = [
        ("a-private-owner", IDENTITY_A, "user_private", 7, "secondary_market", "deal-a"),
        ("b-private-owner", IDENTITY_B, "user_private", 7, "secondary_market", "deal-a"),
        ("unscoped-owner", None, "user_private", 7, "secondary_market", "deal-a"),
        ("a-private-other-user", IDENTITY_A, "user_private", 8, "secondary_market", "deal-a"),
        ("a-project-same-group", IDENTITY_A, "project_shared", None, "secondary_market", "deal-a"),
        ("a-project-other-group", IDENTITY_A, "project_shared", None, "primary_market", "deal-a"),
        ("a-system-other-group", IDENTITY_A, "system_shared", None, "primary_market", None),
        ("a-system-other-tenant", IDENTITY_A, "system_shared", None, "primary_market", None),
    ]
    for index, (source_id, identity, visibility, owner, group, deal) in enumerate(cases, start=1):
        rows.append(
            {
                "source_id": source_id,
                "memory_id": index,
                "identity": identity,
                "visibility": visibility,
                "owner_user_id": owner,
                "agent_group": group,
                "deal_id": deal,
                "tenant_id": "tenant-a" if source_id != "a-system-other-tenant" else "tenant-b",
                "profile": "siq_assistant",
                "content": f"dual-backend identity contract marker {source_id}",
                "vector": [1.0, 0.0, 0.0, 0.0],
            }
        )
    return rows


def _expected_cases() -> list[dict[str, Any]]:
    return [
        {
            "name": "complete_identity_a",
            "identity": IDENTITY_A,
            "expected": ["a-private-owner", "a-project-same-group", "a-system-other-group"],
        },
        {"name": "complete_identity_b", "identity": IDENTITY_B, "expected": ["b-private-owner"]},
        {"name": "unscoped", "identity": None, "expected": ["unscoped-owner"]},
        {"name": "partial_identity", "identity": {"market": "HK"}, "expected": []},
    ]


def _create_postgres_fixture(sync_url: str, schema: str, rows: list[dict[str, Any]]) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {_ident(schema)}"))
        conn.execute(
            text(
                f"""
                CREATE TABLE {schema}.memory_items (
                    id BIGINT PRIMARY KEY, tenant_id TEXT NOT NULL, owner_user_id INTEGER,
                    profile TEXT NOT NULL, agent_group TEXT NOT NULL, visibility TEXT NOT NULL,
                    deal_id TEXT, project_id TEXT, memory_type TEXT NOT NULL, title TEXT,
                    content TEXT NOT NULL, normalized_content TEXT, source_type TEXT,
                    source_id TEXT, confidence DOUBLE PRECISION NOT NULL, importance DOUBLE PRECISION NOT NULL,
                    status TEXT NOT NULL, metadata_json JSONB NOT NULL, valid_from TIMESTAMPTZ,
                    valid_until TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), deleted_at TIMESTAMPTZ
                );
                CREATE TABLE {schema}.memory_embeddings (
                    id BIGSERIAL PRIMARY KEY, memory_id BIGINT NOT NULL, embedding_model TEXT NOT NULL,
                    embedding vector({DIMENSION}) NOT NULL, content_hash TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                );
                """
            )
        )
        for row in rows:
            identity = row["identity"] or {}
            metadata = {"research_identity": identity} if identity else {}
            conn.execute(
                text(
                    f"""
                    INSERT INTO {schema}.memory_items
                    (id, tenant_id, owner_user_id, profile, agent_group, visibility, deal_id, project_id,
                     memory_type, title, content, normalized_content, source_type, source_id, confidence,
                     importance, status, metadata_json)
                    VALUES (:id, :tenant_id, :owner_user_id, :profile, :agent_group, :visibility, :deal_id, NULL,
                            'note', :title, :content, :normalized_content, 'memory_item', :source_id, 0.9,
                            0.8, 'active', CAST(:metadata_json AS jsonb))
                    """
                ),
                {
                    "id": row["memory_id"],
                    "tenant_id": row["tenant_id"],
                    "owner_user_id": row["owner_user_id"],
                    "profile": row["profile"],
                    "agent_group": row["agent_group"],
                    "visibility": row["visibility"],
                    "deal_id": row["deal_id"],
                    "title": row["source_id"],
                    "content": row["content"],
                    "normalized_content": row["content"].lower(),
                    "source_id": row["source_id"],
                    "metadata_json": json.dumps(metadata),
                },
            )
            conn.execute(
                text(
                    f"""
                    INSERT INTO {schema}.memory_embeddings
                    (memory_id, embedding_model, embedding, content_hash)
                    VALUES (:memory_id, 'dual-backend-fixture', CAST(:embedding AS vector), :content_hash)
                    """
                ),
                {
                    "memory_id": row["memory_id"],
                    "embedding": "[1,0,0,0]",
                    "content_hash": row["source_id"],
                },
            )
    engine.dispose()


def _create_milvus_fixture(collection: str, rows: list[dict[str, Any]]) -> Any:
    os.environ["SIQ_AGENT_MEMORY_VECTOR_BACKEND"] = "milvus"
    os.environ["SIQ_AGENT_MEMORY_MILVUS_COLLECTION"] = collection
    os.environ["SIQ_AGENT_MEMORY_EMBEDDING_DIM"] = str(DIMENSION)
    client = agent_memory_milvus._client()
    agent_memory_milvus.create_versioned_collection(
        client=client, name=collection, dimension=DIMENSION, require_absent=True
    )
    records = []
    for row in rows:
        identity = row["identity"] or {}
        records.append(
            agent_memory_milvus.AgentMemoryVectorRecord(
                id=f"dual:{row['source_id']}",
                vector=row["vector"],
                tenant_id=row["tenant_id"],
                visibility=row["visibility"],
                owner_user_id=str(row["owner_user_id"] or ""),
                profile=row["profile"],
                agent_group=row["agent_group"],
                deal_id=row["deal_id"] or "",
                memory_type="note",
                source_kind="memory_item",
                source_id=row["source_id"],
                content_hash=row["source_id"],
                title=row["source_id"],
                content=row["content"],
                metadata_json=json.dumps({"research_identity": identity}),
                research_market=identity.get("market", ""),
                research_company_id=identity.get("company_id", ""),
                research_filing_id=identity.get("filing_id", ""),
                research_parse_run_id=identity.get("parse_run_id", ""),
            )
        )
    agent_memory_milvus.upsert_records(records, flush=True)
    return client


async def _search_backend(
    async_session: Any,
    *,
    backend: str,
    context: agent_memory_service.MemoryRequestContext,
) -> dict[str, Any]:
    os.environ["SIQ_AGENT_MEMORY_VECTOR_BACKEND"] = backend
    result = await agent_memory_service.search_memory_items(
        async_session, context, query="dual-backend identity contract marker", limit=25
    )
    dense_prefix = "milvus" if backend == "milvus" else "pgvector"
    return {
        "source_ids": sorted(str(item.get("source_id") or "") for item in result),
        "dense_source_ids": sorted(
            str(item.get("source_id") or "")
            for item in result
            if str(item.get("retrieval_source") or "").startswith(dense_prefix)
        ),
        "retrieval_sources": sorted(
            {
                str(item.get("retrieval_source") or "unknown")
                for item in result
            }
        ),
    }


async def _run_searches(async_url: str, schema: str, collection: str) -> dict[str, Any]:
    os.environ["SIQ_AGENT_MEMORY_SCHEMA"] = schema
    os.environ["SIQ_AGENT_MEMORY_MILVUS_COLLECTION"] = collection
    os.environ["SIQ_AGENT_MEMORY_EMBEDDING_DIM"] = str(DIMENSION)
    os.environ["SIQ_AGENT_MEMORY_RERANK_ENABLED"] = "false"

    async_engine = create_async_engine(async_url)
    session_factory = async_sessionmaker(async_engine, expire_on_commit=False)
    outcomes: list[dict[str, Any]] = []
    try:
        for case in _expected_cases():
            context = agent_memory_service.MemoryRequestContext(
                tenant_id="tenant-a",
                user_id=7,
                profile="siq_assistant",
                agent_group="secondary_market",
                session_id="dual-backend-verification",
                deal_id="deal-a",
                visibility="user_private",
                market=(case["identity"] or {}).get("market"),
                company_id=(case["identity"] or {}).get("company_id"),
                filing_id=(case["identity"] or {}).get("filing_id"),
                parse_run_id=(case["identity"] or {}).get("parse_run_id"),
            )
            for backend in ("pgvector", "milvus"):
                async with session_factory() as session:
                    # The production embedder is intentionally not contacted.  The
                    # fixture vector is identical in both backends.
                    async def fake_embed(_query: str) -> list[float]:
                        return [1.0, 0.0, 0.0, 0.0]

                    original_embed = agent_memory_service._embed_text
                    agent_memory_service._embed_text = fake_embed
                    try:
                        hits = await _search_backend(session, backend=backend, context=context)
                    finally:
                        agent_memory_service._embed_text = original_embed
                outcomes.append(
                    {
                        "case": case["name"],
                        "backend": backend,
                        "expected_source_ids": sorted(case["expected"]),
                        "observed_source_ids": hits["source_ids"],
                        "dense_source_ids": hits["dense_source_ids"],
                        "retrieval_sources": hits["retrieval_sources"],
                        "passed": (
                            hits["source_ids"] == sorted(case["expected"])
                            and (
                                not case["expected"]
                                or hits["dense_source_ids"] == sorted(case["expected"])
                            )
                        ),
                    }
                )
    finally:
        await async_engine.dispose()
    return {
        "matrix": outcomes,
        "passed": bool(outcomes) and all(item["passed"] for item in outcomes),
    }


def _drop_postgres_schema(sync_url: str, schema: str) -> None:
    engine = create_engine(sync_url)
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {_ident(schema)} CASCADE"))
    engine.dispose()


def _redacted_error(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": str(exc)[:500]}


def run(*, postgres_url: str, output: Path) -> dict[str, Any]:
    token = secrets.token_hex(8)
    schema = f"agent_memory_dual_{token}"
    collection = f"siq_agent_memory_dual_{token}"
    rows = _fixture()
    client = None
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "passed": False,
        "disposable": {"postgres_schema": schema, "milvus_collection": collection},
        "backends": {"postgresql": "pgvector", "milvus": True},
        "matrix": [],
        "cleanup": {"postgres_schema_dropped": False, "milvus_collection_dropped": False},
    }
    try:
        _create_postgres_fixture(_sync_url(postgres_url), schema, rows)
        client = _create_milvus_fixture(collection, rows)
        report.update(asyncio.run(_run_searches(_async_url(postgres_url), schema, collection)))
    except Exception as exc:
        report["error"] = _redacted_error(exc)
    finally:
        try:
            if client is not None and client.has_collection(collection):
                client.drop_collection(collection)
                report["cleanup"]["milvus_collection_dropped"] = True
        except Exception as exc:
            report["cleanup"]["milvus_error"] = _redacted_error(exc)
        try:
            _drop_postgres_schema(_sync_url(postgres_url), schema)
            report["cleanup"]["postgres_schema_dropped"] = True
        except Exception as exc:
            report["cleanup"]["postgres_error"] = _redacted_error(exc)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--postgres-url",
        default=os.getenv("SIQ_DUAL_BACKEND_POSTGRES_URL") or os.getenv("SIQ_APP_DATABASE_URL"),
        help="Disposable PostgreSQL URL with pgvector installed (or SIQ_DUAL_BACKEND_POSTGRES_URL)",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if not args.postgres_url:
        parser.error("--postgres-url or SIQ_DUAL_BACKEND_POSTGRES_URL is required")
    report = run(postgres_url=args.postgres_url, output=args.output)
    print(json.dumps({"passed": report.get("passed"), "output": str(args.output)}, ensure_ascii=False))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
