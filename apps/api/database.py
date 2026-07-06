from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import inspect, text
import os
import re

from services.path_config import BACKEND_DATA_ROOT

DB_DIR = BACKEND_DATA_ROOT
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "agent.db"

# API 应用状态库：认证、聊天、审计、workspace、usage 等表。
# SIQ_APP_DATABASE_URL 优先，DATABASE_URL 仅保留为兼容入口；市场事实库导入不应复用这里的连接串。
APP_DATABASE_URL = os.getenv("SIQ_APP_DATABASE_URL") or os.getenv("DATABASE_URL") or f"sqlite:///{DB_PATH}"
DATABASE_URL = APP_DATABASE_URL

# 异步数据库URL
if DATABASE_URL.startswith("postgresql"):
    ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql+psycopg://", "postgresql+asyncpg://")
elif DATABASE_URL.startswith("sqlite"):
    ASYNC_DATABASE_URL = DATABASE_URL.replace("sqlite://", "sqlite+aiosqlite://", 1)
else:
    ASYNC_DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, echo=False)
async_engine = create_async_engine(ASYNC_DATABASE_URL, echo=False)


def create_db_and_tables():
    import models  # noqa: F401
    import services.auth_service  # noqa: F401
    import services.usage_service  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _ensure_auth_columns()
    _ensure_chat_message_columns()
    _ensure_agent_memory_schema()


def _ensure_auth_columns():
    inspector = inspect(engine)
    if not inspector.has_table("users"):
        return

    columns = {column["name"] for column in inspector.get_columns("users")}
    additions = []
    if "approval_status" not in columns:
        additions.append(("approval_status", "VARCHAR(20) DEFAULT 'approved'"))
    if "approval_note" not in columns:
        additions.append(("approval_note", "VARCHAR(500)"))
    if "approved_by" not in columns:
        additions.append(("approved_by", "INTEGER"))
    if "approved_at" not in columns:
        additions.append(("approved_at", "TIMESTAMP"))

    if not additions:
        return

    with engine.begin() as connection:
        for name, definition in additions:
            connection.execute(text(f"ALTER TABLE users ADD COLUMN {name} {definition}"))


def _ensure_chat_message_columns():
    inspector = inspect(engine)
    if not inspector.has_table("chatmessage"):
        return

    columns = {column["name"] for column in inspector.get_columns("chatmessage")}
    additions = []
    if "attachments_json" not in columns:
        additions.append(("attachments_json", "TEXT"))

    if not additions:
        return

    with engine.begin() as connection:
        for name, definition in additions:
            connection.execute(text(f"ALTER TABLE chatmessage ADD COLUMN {name} {definition}"))


def _agent_memory_schema_name() -> str:
    schema = os.getenv("SIQ_AGENT_MEMORY_SCHEMA", "agent_memory").strip() or "agent_memory"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise RuntimeError(f"Invalid SIQ_AGENT_MEMORY_SCHEMA: {schema!r}")
    return schema


def _agent_memory_embedding_dim() -> int:
    raw_value = os.getenv("SIQ_AGENT_MEMORY_EMBEDDING_DIM", "1536")
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"Invalid SIQ_AGENT_MEMORY_EMBEDDING_DIM: {raw_value!r}") from exc
    if value <= 0 or value > 16384:
        raise RuntimeError("SIQ_AGENT_MEMORY_EMBEDDING_DIM must be between 1 and 16384")
    return value


def _ensure_agent_memory_schema():
    if engine.dialect.name != "postgresql":
        return

    schema = _agent_memory_schema_name()
    embedding_dim = _agent_memory_embedding_dim()
    vector_backend = os.getenv("SIQ_AGENT_MEMORY_VECTOR_BACKEND", "milvus").strip().lower()
    pgvector_enabled = (
        vector_backend == "pgvector"
        or os.getenv("SIQ_AGENT_MEMORY_PGVECTOR_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    )
    vector_available = pgvector_enabled

    with engine.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))

    if pgvector_enabled:
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception as exc:  # pragma: no cover - depends on the deployed Postgres image
            vector_available = False
            print(f"[agent-memory] pgvector extension is unavailable; vector table creation skipped: {exc}")

    ddl_statements = [
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.sessions (
            id BIGSERIAL PRIMARY KEY,
            session_id TEXT NOT NULL UNIQUE,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            user_id INTEGER,
            profile TEXT NOT NULL,
            agent_group TEXT NOT NULL DEFAULT 'secondary_market',
            title TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            visibility TEXT NOT NULL DEFAULT 'user_private',
            deal_id TEXT,
            project_id TEXT,
            metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_active_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.messages (
            id BIGSERIAL PRIMARY KEY,
            session_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            user_id INTEGER,
            profile TEXT NOT NULL,
            agent_group TEXT NOT NULL DEFAULT 'secondary_market',
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            attachments_json JSONB,
            token_count INTEGER,
            model_name TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.runs (
            id BIGSERIAL PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE,
            session_id TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            user_id INTEGER,
            profile TEXT NOT NULL,
            agent_group TEXT NOT NULL DEFAULT 'secondary_market',
            deal_id TEXT,
            project_id TEXT,
            task_type TEXT,
            status TEXT NOT NULL DEFAULT 'running',
            input_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            output_json JSONB,
            error_json JSONB,
            started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at TIMESTAMPTZ
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.tool_events (
            id BIGSERIAL PRIMARY KEY,
            run_id TEXT,
            session_id TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            user_id INTEGER,
            profile TEXT,
            tool_name TEXT NOT NULL,
            tool_input_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            tool_output_ref TEXT,
            status TEXT NOT NULL DEFAULT 'ok',
            latency_ms INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.session_summaries (
            id BIGSERIAL PRIMARY KEY,
            session_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            user_id INTEGER,
            profile TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            last_message_id BIGINT,
            message_count INTEGER NOT NULL DEFAULT 0,
            summary_version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, user_id, profile, session_id)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.memory_items (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            owner_user_id INTEGER,
            created_by INTEGER,
            profile TEXT NOT NULL,
            agent_group TEXT NOT NULL DEFAULT 'secondary_market',
            visibility TEXT NOT NULL DEFAULT 'user_private',
            deal_id TEXT,
            project_id TEXT,
            memory_type TEXT NOT NULL DEFAULT 'note',
            title TEXT,
            content TEXT NOT NULL,
            normalized_content TEXT,
            source_type TEXT,
            source_id TEXT,
            confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
            valid_from TIMESTAMPTZ,
            valid_until TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'candidate',
            metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at TIMESTAMPTZ
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.memory_links (
            id BIGSERIAL PRIMARY KEY,
            memory_id BIGINT NOT NULL,
            link_type TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT,
            target_uri TEXT,
            metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.access_bindings (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            principal_type TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, resource_type, resource_id, principal_type, principal_id, role)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.feedback_events (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'default',
            user_id INTEGER,
            memory_id BIGINT,
            session_id TEXT,
            feedback_type TEXT NOT NULL,
            feedback_text TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
    ]

    if vector_available:
        ddl_statements.append(
            f"""
            CREATE TABLE IF NOT EXISTS {schema}.memory_embeddings (
                id BIGSERIAL PRIMARY KEY,
                memory_id BIGINT NOT NULL,
                embedding_model TEXT NOT NULL,
                embedding vector({embedding_dim}) NOT NULL,
                content_hash TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

    index_statements = [
        f"CREATE INDEX IF NOT EXISTS idx_agent_memory_sessions_user ON {schema}.sessions (tenant_id, user_id, profile, last_active_at)",
        f"CREATE INDEX IF NOT EXISTS idx_agent_memory_sessions_project ON {schema}.sessions (tenant_id, deal_id, profile)",
        f"CREATE INDEX IF NOT EXISTS idx_agent_memory_messages_session ON {schema}.messages (tenant_id, user_id, profile, session_id, created_at)",
        f"CREATE INDEX IF NOT EXISTS idx_agent_memory_runs_session ON {schema}.runs (tenant_id, user_id, profile, session_id, started_at)",
        f"CREATE INDEX IF NOT EXISTS idx_agent_memory_items_private ON {schema}.memory_items (tenant_id, owner_user_id, profile, status, updated_at)",
        f"CREATE INDEX IF NOT EXISTS idx_agent_memory_items_project ON {schema}.memory_items (tenant_id, deal_id, project_id, visibility, status, updated_at)",
        f"CREATE INDEX IF NOT EXISTS idx_agent_memory_links_memory ON {schema}.memory_links (memory_id, target_type)",
        f"CREATE INDEX IF NOT EXISTS idx_agent_memory_access_resource ON {schema}.access_bindings (tenant_id, resource_type, resource_id)",
    ]
    if vector_available:
        index_statements.append(
            f"CREATE INDEX IF NOT EXISTS idx_agent_memory_embeddings_vector ON {schema}.memory_embeddings USING ivfflat (embedding vector_cosine_ops)"
        )

    with engine.begin() as connection:
        for statement in ddl_statements + index_statements:
            connection.execute(text(statement))


def get_session():
    with Session(engine) as session:
        yield session


async def get_async_session():
    async with AsyncSession(async_engine) as session:
        yield session
