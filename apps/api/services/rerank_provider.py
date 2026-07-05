"""Optional rerank provider adapter for SIQ retrieval."""

from __future__ import annotations

import os
from typing import Any

import httpx


RERANK_RESULT_SCHEMA = "siq_rerank_result_v1"
DEFAULT_RERANK_PATH = "/v1/rerank"
MAX_RERANK_CANDIDATES = 50


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _endpoint() -> str:
    base = str(os.getenv("SIQ_RERANK_BASE_URL") or os.getenv("RERANK_BASE_URL") or "").strip().rstrip("/")
    if not base:
        return ""
    path = str(os.getenv("SIQ_RERANK_PATH") or DEFAULT_RERANK_PATH).strip()
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _candidate_text(candidate: dict[str, Any]) -> str:
    return str(
        candidate.get("text")
        or candidate.get("quote_preview")
        or candidate.get("snippet")
        or candidate.get("claim")
        or candidate.get("title")
        or ""
    ).strip()


def rerank_candidates(
    *,
    query: str,
    candidates: list[dict[str, Any]],
    enabled: bool = False,
    top_n: int | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    normalized_candidates = [item for item in candidates[:MAX_RERANK_CANDIDATES] if isinstance(item, dict)]
    endpoint = _endpoint()
    configured = bool(endpoint)
    should_run = bool(enabled or _env_bool("SIQ_RERANK_ENABLED"))
    if not should_run:
        return {
            "schema_version": RERANK_RESULT_SCHEMA,
            "enabled": False,
            "configured": configured,
            "status": "skipped",
            "reason": "rerank_disabled",
            "results": normalized_candidates[: top_n or len(normalized_candidates)],
            "result_count": min(len(normalized_candidates), top_n or len(normalized_candidates)),
        }
    if not configured:
        return {
            "schema_version": RERANK_RESULT_SCHEMA,
            "enabled": True,
            "configured": False,
            "status": "skipped",
            "reason": "rerank_endpoint_not_configured",
            "results": normalized_candidates[: top_n or len(normalized_candidates)],
            "result_count": min(len(normalized_candidates), top_n or len(normalized_candidates)),
        }

    documents = [{"text": _candidate_text(item)} for item in normalized_candidates]
    try:
        with httpx.Client() as client:
            response = client.post(
                endpoint,
                json={
                    "model": os.getenv("SIQ_RERANK_MODEL") or os.getenv("RERANK_MODEL"),
                    "query": str(query or "")[:600],
                    "documents": documents,
                    "top_n": top_n,
                    "return_sigmoid": True,
                },
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        return {
            "schema_version": RERANK_RESULT_SCHEMA,
            "enabled": True,
            "configured": True,
            "status": "error",
            "reason": "rerank_request_failed",
            "error": str(exc)[:300],
            "results": normalized_candidates[: top_n or len(normalized_candidates)],
            "result_count": min(len(normalized_candidates), top_n or len(normalized_candidates)),
        }

    raw_items = (payload.get("data") or payload.get("results")) if isinstance(payload, dict) else []
    reranked: list[dict[str, Any]] = []
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(normalized_candidates):
            continue
        candidate = dict(normalized_candidates[index])
        score = item.get("relevance_score", item.get("score"))
        candidate["rerank_score"] = score
        reranked.append(candidate)
    if not reranked:
        reranked = normalized_candidates
    if top_n is not None:
        reranked = reranked[: max(1, int(top_n))]
    return {
        "schema_version": RERANK_RESULT_SCHEMA,
        "enabled": True,
        "configured": True,
        "status": "completed",
        "reason": None,
        "results": reranked,
        "result_count": len(reranked),
    }
