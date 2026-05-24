import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from database import create_db_and_tables
from routers import pet, chat, achievements, analysis, factchecker, legal, tracking_agent, wiki, settings, system, downloads, workflow, source
from seed import seed_data

FRONT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "front"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    seed_data()
    yield


app = FastAPI(title="FinSight API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "tauri://localhost", "https://tauri.localhost"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pet.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(achievements.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(factchecker.router, prefix="/api")
app.include_router(legal.router, prefix="/api")
app.include_router(tracking_agent.router, prefix="/api")
app.include_router(wiki.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(system.router, prefix="/api")
app.include_router(downloads.router, prefix="/api")
app.include_router(workflow.router, prefix="/api")
app.include_router(source.router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def chat_page():
    index_path = os.path.join(FRONT_DIR, "index.html")
    return FileResponse(index_path)
