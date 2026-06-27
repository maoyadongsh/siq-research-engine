from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from .adapter import (
    CN_PDF2MD_API_BASE,
    CN_REPORT_FINDER_BASE,
    cn_legacy_entrypoints,
    cn_pdf2md_headers,
)

router = APIRouter(prefix="/markets/cn", tags=["cn-market-adapter"])

REPORT_FINDER_TIMEOUT = float(__import__("os").environ.get("SIQ_CN_REPORT_FINDER_TIMEOUT", "90"))
PDF2MD_PROXY_TIMEOUT = float(__import__("os").environ.get("SIQ_CN_PDF2MD_PROXY_TIMEOUT", "300"))


def _content_type(headers: httpx.Headers) -> str:
    return headers.get("content-type") or "application/octet-stream"


async def _proxy_json(
    *,
    base_url: str,
    upstream_path: str,
    request: Request,
    timeout: float,
    headers: dict[str, str] | None = None,
) -> Response:
    method = request.method
    params = list(request.query_params.multi_items())
    body = await request.body() if method in {"POST", "PUT", "PATCH", "DELETE"} else None
    outbound_headers = dict(headers or {})
    content_type = request.headers.get("content-type")
    if content_type:
        outbound_headers.setdefault("content-type", content_type)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            upstream = await client.request(
                method,
                f"{base_url}{upstream_path}",
                params=params,
                content=body,
                headers=outbound_headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"CN adapter upstream unavailable: {exc}") from exc
    return Response(
        content=b"" if method == "HEAD" else upstream.content,
        status_code=upstream.status_code,
        media_type=_content_type(upstream.headers),
    )


@router.get("/entrypoints")
def cn_entrypoints() -> dict[str, Any]:
    return cn_legacy_entrypoints()


@router.get("/rules")
def cn_rules() -> dict[str, Any]:
    entrypoints = cn_legacy_entrypoints()
    return {
        "market": "CN",
        "rule_version": entrypoints["rule_version"],
        "rule_source": "apps/pdf-parser/financial_extractor.py",
        "financial_artifacts": ["financial_data.json", "financial_checks.json", "quality_report.json"],
        "adapter": entrypoints,
    }


@router.api_route("/finder/{upstream_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def proxy_cn_finder(upstream_path: str, request: Request) -> Response:
    return await _proxy_json(
        base_url=CN_REPORT_FINDER_BASE,
        upstream_path=f"/{upstream_path}",
        request=request,
        timeout=REPORT_FINDER_TIMEOUT,
    )


@router.api_route("/pdf/{upstream_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"])
async def proxy_cn_pdf_parser(upstream_path: str, request: Request) -> Response:
    return await _proxy_json(
        base_url=CN_PDF2MD_API_BASE,
        upstream_path=f"/api/{upstream_path}",
        request=request,
        timeout=PDF2MD_PROXY_TIMEOUT,
        headers=cn_pdf2md_headers(),
    )
