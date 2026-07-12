#!/usr/bin/env python3
"""Container-side fixtures for the upload proxy resource smoke."""

import argparse
import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Annotated, Any


def run_upstream(*, port: int, slow_requests: int, delay_seconds: float) -> None:
    state = {"requests": 0}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._json(200, {"status": "ok", **state})
                return
            self._json(404, {"detail": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/tasks":
                self._json(404, {"detail": "not found"})
                return
            remaining = max(0, int(self.headers.get("Content-Length") or 0))
            while remaining:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
            with lock:
                state["requests"] += 1
                request_number = state["requests"]
            if request_number <= slow_requests:
                time.sleep(delay_seconds)
            try:
                self._json(200, {"tasks": [{"task_id": f"smoke-{request_number}"}]})
            except (BrokenPipeError, ConnectionResetError):
                return

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


def create_proxy_app():
    import httpx
    from fastapi import FastAPI, File, HTTPException, Request, UploadFile
    from fastapi.responses import JSONResponse
    from services.upload_proxy_limits import (
        UploadProxyConcurrencyLimiter,
        buffer_upload_files,
        close_buffered_uploads,
    )

    max_concurrency = int(os.environ.get("SMOKE_MAX_CONCURRENCY", "2"))
    queue_timeout = float(os.environ.get("SMOKE_QUEUE_TIMEOUT_SECONDS", "0.25"))
    upstream_url = os.environ["SMOKE_UPSTREAM_URL"]
    upstream_read_timeout = float(os.environ.get("SMOKE_UPSTREAM_READ_TIMEOUT_SECONDS", "0.75"))
    spool_max_bytes = int(os.environ.get("SMOKE_SPOOL_MAX_BYTES", str(1024 * 1024)))
    limiter = UploadProxyConcurrencyLimiter(
        max_concurrency=max_concurrency,
        queue_timeout_seconds=queue_timeout,
    )
    app = FastAPI()
    state: dict[str, Any] = {
        "active": 0,
        "max_active": 0,
        "admitted": 0,
        "busy_rejections": 0,
        "buffered_files": 0,
        "rolled_to_disk": 0,
        "closed_files": 0,
        "upstream_timeouts": 0,
    }
    state_lock = asyncio.Lock()

    @app.exception_handler(HTTPException)
    async def handle_http_exception(_request: Request, exc: HTTPException):
        if exc.status_code == 503 and isinstance(exc.detail, dict):
            if exc.detail.get("error") == "upload_proxy_busy":
                async with state_lock:
                    state["busy_rejections"] += 1
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> dict[str, Any]:
        async with state_lock:
            return {
                **state,
                "limit": max_concurrency,
                "queue_timeout_seconds": queue_timeout,
                "spool_max_bytes": spool_max_bytes,
            }

    @app.post("/upload")
    async def upload(files: Annotated[list[UploadFile], File(...)]):
        async with limiter.slot():
            async with state_lock:
                state["active"] += 1
                state["admitted"] += 1
                state["max_active"] = max(state["max_active"], state["active"])
            buffered = []
            try:
                buffered = await buffer_upload_files(
                    files,
                    max_file_bytes=32 * 1024 * 1024,
                    max_batch_bytes=64 * 1024 * 1024,
                    spool_max_bytes=spool_max_bytes,
                )
                async with state_lock:
                    state["buffered_files"] += len(buffered)
                    state["rolled_to_disk"] += sum(
                        bool(getattr(item.file, "_rolled", False)) for item in buffered
                    )
                timeout = httpx.Timeout(connect=2.0, write=5.0, read=upstream_read_timeout, pool=2.0)
                multipart = [
                    ("files", (item.filename, item.file, item.content_type))
                    for item in buffered
                ]
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(upstream_url, files=multipart)
                return JSONResponse(status_code=response.status_code, content=response.json())
            except httpx.RequestError as exc:
                async with state_lock:
                    state["upstream_timeouts"] += 1
                return JSONResponse(
                    status_code=502,
                    content={"detail": "controlled upstream unavailable", "type": type(exc).__name__},
                )
            finally:
                close_buffered_uploads(buffered)
                async with state_lock:
                    state["closed_files"] += sum(item.file.closed for item in buffered)
                    state["active"] -= 1

    return app


def run_proxy(*, port: int) -> None:
    import uvicorn

    uvicorn.run(create_proxy_app(), host="0.0.0.0", port=port, log_level="warning")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    upstream = subparsers.add_parser("upstream")
    upstream.add_argument("--port", type=int, default=18080)
    upstream.add_argument("--slow-requests", type=int, default=2)
    upstream.add_argument("--delay-seconds", type=float, default=2.0)
    proxy = subparsers.add_parser("proxy")
    proxy.add_argument("--port", type=int, default=18081)
    args = parser.parse_args()
    if args.mode == "upstream":
        run_upstream(port=args.port, slow_requests=args.slow_requests, delay_seconds=args.delay_seconds)
    else:
        run_proxy(port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
