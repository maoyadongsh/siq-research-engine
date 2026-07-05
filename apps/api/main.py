import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from database import create_db_and_tables
from routers import agent, chat, achievements, analysis, factchecker, legal, tracking_agent, wiki, settings, system, downloads, workflow, source, eval_e2e, auth, workspace, market_reports, document_parser, deals, primary_market_meeting
from services.auth_dependencies import get_current_user
from services.auth_service import AuthService
from services.path_config import FRONTEND_ROOT
from seed import seed_data

FRONT_DIR = str(FRONTEND_ROOT)


@asynccontextmanager
async def lifespan(app: FastAPI):
    AuthService.validate_runtime_config()
    create_db_and_tables()
    seed_data()
    yield


app = FastAPI(title="SIQ API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:15173",
        "http://127.0.0.1:15173",
        "tauri://localhost",
        "https://tauri.localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 认证路由（无需认证）
app.include_router(auth.router, prefix="/api/auth")

# 只读展示路由。报告 iframe 入口保持公开；来源/PDF 页码链接在 source router
# 内做任务归属校验，并使用短期签名 token 支持无 Bearer 头的页面跳转。
app.include_router(wiki.router, prefix="/api")
app.include_router(source.router, prefix="/api")

# 业务路由
app.include_router(agent.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(chat.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(achievements.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(analysis.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(factchecker.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(legal.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(tracking_agent.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(settings.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(system.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(market_reports.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(document_parser.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(deals.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(primary_market_meeting.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(downloads.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(workflow.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(eval_e2e.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(workspace.router, prefix="/api", dependencies=[Depends(get_current_user)])
app.include_router(workspace.pdf_router, prefix="/api", dependencies=[Depends(get_current_user)])


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def chat_page():
    index_path = os.path.join(FRONT_DIR, "index.html")
    return FileResponse(index_path)
