from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import inspect, text
import os

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


def get_session():
    with Session(engine) as session:
        yield session


async def get_async_session():
    async with AsyncSession(async_engine) as session:
        yield session
