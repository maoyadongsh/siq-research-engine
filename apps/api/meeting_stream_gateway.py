"""Standalone ASGI process for meeting WebSocket capture and audio replay."""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager

from database import create_db_and_tables
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from routers.meeting_stream import gateway_router
from services.meeting_config import MeetingSettings
from services.meeting_metrics import render_meeting_process_metrics
from services.meeting_stream_deployment import meeting_stream_gateway_mode
from services.runtime_security import validate_runtime_security_config


@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_runtime_security_config()
    if meeting_stream_gateway_mode(configured="external") != "external":
        raise RuntimeError("meeting stream gateway must run in external mode")
    create_db_and_tables()
    yield


app = FastAPI(
    title="SIQ Meeting Stream Gateway",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.include_router(gateway_router, prefix="/api")


def _require_metrics_access(request: Request) -> None:
    expected = str(os.getenv("SIQ_METRICS_TOKEN") or os.getenv("SIQ_INTERNAL_METRICS_TOKEN") or "").strip()
    protected = str(os.getenv("SIQ_DEPLOYMENT_PROFILE") or "development").strip().lower() in {
        "docker",
        "prod",
        "production",
    }
    if not expected:
        if protected:
            raise HTTPException(503, "Metrics authentication is not configured")
        return
    supplied = str(request.headers.get("X-SIQ-Service-Token") or "").strip()
    authorization = str(request.headers.get("Authorization") or "")
    if not supplied and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "Metrics authentication required")


@app.get(
    "/metrics",
    response_class=PlainTextResponse,
    dependencies=[Depends(_require_metrics_access)],
    include_in_schema=False,
)
def metrics() -> PlainTextResponse:
    return PlainTextResponse(
        render_meeting_process_metrics(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/health/live", include_in_schema=False)
def liveness() -> dict[str, str]:
    return {"status": "ok", "component": "meeting_stream_gateway"}


@app.get("/health/ready", include_in_schema=False)
def readiness() -> dict[str, object]:
    settings = MeetingSettings.from_env()
    return {
        "status": "ready" if settings.operational else "unavailable",
        "component": "meeting_stream_gateway",
        "core_ready": settings.operational,
        "realtime_asr": settings.operational and settings.asr_enabled,
        "configuration_errors": list(settings.errors),
    }


@app.get("/health", include_in_schema=False)
def health() -> dict[str, object]:
    return readiness()
