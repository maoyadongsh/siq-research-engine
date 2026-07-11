import os
import logging
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from database import create_db_and_tables
from routers import agent, chat, achievements, analysis, factchecker, legal, tracking_agent, wiki, settings, system, downloads, workflow, source, eval_e2e, auth, workspace, market_reports, document_parser, deals, primary_market_meeting
from services.auth_dependencies import get_current_user
from services.auth_service import AuthService
from services.observability import (
    REQUEST_ID_HEADER,
    emit_json_log,
    metrics_snapshot,
    monotonic_ms,
    normalize_request_id,
    record_http_request,
    render_prometheus_metrics,
    reset_request_id,
    set_request_id,
)
from services.path_config import FRONTEND_ROOT
from services.runtime_security import cors_origins_from_env, validate_runtime_security_config
from seed import seed_data

FRONT_DIR = str(FRONTEND_ROOT)
logger = logging.getLogger("siq.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    AuthService.validate_runtime_config()
    validate_runtime_security_config()
    create_db_and_tables()
    seed_data()
    yield


app = FastAPI(title="SIQ API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins_from_env(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_observability_middleware(request, call_next):
    request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
    request_id_token = set_request_id(request_id)
    start = time.perf_counter()
    status_code = 500
    recorded = False
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
    except Exception:
        duration_ms = monotonic_ms(start)
        record_http_request(request.method, request.url.path, status_code, duration_ms)
        recorded = True
        emit_json_log(
            logger,
            "api_request_failed",
            method=request.method,
            path=request.url.path,
            status_code=status_code,
            duration_ms=duration_ms,
        )
        raise
    finally:
        duration_ms = monotonic_ms(start)
        if not recorded:
            record_http_request(request.method, request.url.path, status_code, duration_ms)
        if status_code < 500:
            emit_json_log(
                logger,
                "api_request_completed",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
            )
        reset_request_id(request_id_token)

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
    snapshot = metrics_snapshot()
    return {
        "status": "ok",
        "uptime_seconds": snapshot["uptime_seconds"],
        "requests_total": snapshot["request_count"],
        "request_errors_total": snapshot["request_error_count"],
        "answer_traces_total": snapshot["answer_trace_count"],
    }


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    return PlainTextResponse(render_prometheus_metrics(), media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/", include_in_schema=False)
def chat_page():
    index_path = os.path.join(FRONT_DIR, "index.html")
    return FileResponse(index_path)
