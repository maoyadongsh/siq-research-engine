from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException, Request, Response


def content_type(headers: httpx.Headers) -> str:
    return headers.get("content-type") or "application/octet-stream"


async def proxy_request(
    *,
    base_url: str,
    upstream_path: str,
    request: Request,
    timeout: float,
) -> Response:
    method = request.method
    params = list(request.query_params.multi_items())
    body = await request.body() if method in {"POST", "PUT", "PATCH", "DELETE"} else None
    headers: dict[str, str] = {}
    request_content_type = request.headers.get("content-type")
    if request_content_type:
        headers["content-type"] = request_content_type
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream = await client.request(
                method,
                f"{base_url}{upstream_path}",
                params=params,
                content=body,
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Market report upstream unavailable: {exc}") from exc
    return Response(
        content=b"" if method == "HEAD" else upstream.content,
        status_code=upstream.status_code,
        media_type=content_type(upstream.headers),
    )


async def finder_assist(*, report_finder_base: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream = await client.post(f"{report_finder_base}/v1/reports/assist", json=payload)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Market report assist upstream unavailable: {exc}") from exc
    if upstream.status_code >= 400:
        raise HTTPException(status_code=upstream.status_code, detail=upstream.text[:1000])
    if not upstream.content:
        return {}
    try:
        parsed = upstream.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Market report assist returned invalid JSON") from exc
    return parsed if isinstance(parsed, dict) else {}


async def proxy_rules_get(*, market_rules_base: str, upstream_path: str, timeout: float = 10.0) -> Response:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream = await client.get(f"{market_rules_base}{upstream_path}")
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"Market rules service unavailable: {exc}") from exc
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=content_type(upstream.headers),
    )


async def market_report_health(
    *,
    report_finder_base: str,
    market_rules_base: str,
    timeout: float = 5.0,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "report_finder_base": report_finder_base,
        "market_rules_base": market_rules_base,
        "report_finder": {"status": "unknown"},
        "market_rules": {"status": "unknown"},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            finder = await client.get(f"{report_finder_base}/health")
            finder_payload: dict[str, Any] = {}
            try:
                parsed = finder.json()
                if isinstance(parsed, dict):
                    finder_payload = parsed
            except Exception:
                finder_payload = {}
            result["report_finder"] = {
                "status": "ok" if finder.status_code < 400 else "error",
                "code": finder.status_code,
                "config": finder_payload.get("config") or {},
                "markets": finder_payload.get("markets") or {},
            }
        except httpx.RequestError as exc:
            result["report_finder"] = {"status": "error", "error": str(exc)}
        try:
            rules = await client.get(f"{market_rules_base}/healthz")
            result["market_rules"] = {"status": "ok" if rules.status_code < 400 else "error", "code": rules.status_code}
        except httpx.RequestError as exc:
            result["market_rules"] = {"status": "error", "error": str(exc)}
    return result
