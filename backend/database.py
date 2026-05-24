from sqlmodel import SQLModel, Session, create_engine
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
import os

DB_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(DB_DIR, exist_ok=True)
DB_PATH = os.path.join(DB_DIR, "pet.db")

DATABASE_URL = f"sqlite:///{DB_PATH}"
ASYNC_DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, echo=False)
async_engine = create_async_engine(ASYNC_DATABASE_URL, echo=False)


def create_db_and_tables():
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session


async def get_async_session():
    async with AsyncSession(async_engine) as session:
        yield session
