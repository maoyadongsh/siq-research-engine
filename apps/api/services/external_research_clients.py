"""Controlled external research clients for Deal OS.

The module is disabled by default. Callers must explicitly opt in before any
network request is attempted, and credentials are read only from SIQ-managed
environment variables.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx


EXTERNAL_RESEARCH_SCHEMA = "siq_external_research_v1"
EXTERNAL_PROVIDER_RESULT_SCHEMA = "siq_external_research_provider_v1"
DEFAULT_PROVIDERS = ("exa", "tavily", "qcc")
MAX_QUERY_CHARS = 400
MAX_RESULTS = 20


PROVIDER_ENV: dict[str, tuple[str, ...]] = {
    "exa": ("SIQ_EXA_API_KEY", "EXA_API_KEY"),
    "tavily": ("SIQ_TAVILY_API_KEY", "TAVILY_API_KEY"),
    "qcc": ("SIQ_QCC_MCP_CONFIG_PATH", "QCC_MCP_CONFIG_PATH", "SIQ_QCC_MCP_CONFIG_JSON"),
}


def _env_value(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value.strip()
    return ""


def _normalize_query(query: str | None) -> str:
    return " ".join(str(query or "").split())[:MAX_QUERY_CHARS]


def _normalize_max_results(value: int | str | None) -> int:
    try:
        parsed = int(value) if value is not None else 5
    except (TypeError, ValueError):
        parsed = 5
    return max(1, min(parsed, MAX_RESULTS))


def _provider_status(provider: str) -> dict[str, Any]:
    normalized = str(provider or "").strip().lower()
    if normalized not in PROVIDER_ENV:
        return {"provider": normalized, "configured": False, "reason": "unknown_provider"}
    configured = bool(_env_value(PROVIDER_ENV[normalized]))
    return {
        "provider": normalized,
        "configured": configured,
        "credential_env": [name for name in PROVIDER_ENV[normalized] if os.getenv(name)],
    }


def provider_status() -> dict[str, Any]:
    return {
        "schema_version": "siq_external_research_provider_status_v1",
        "providers": [_provider_status(provider) for provider in DEFAULT_PROVIDERS],
    }


def _empty_provider_result(provider: str, *, status: str, reason: str) -> dict[str, Any]:
    return {
        "schema_version": EXTERNAL_PROVIDER_RESULT_SCHEMA,
        "provider": provider,
        "status": status,
        "reason": reason,
        "results": [],
        "result_count": 0,
        "error": None,
    }


def _source_id(provider: str, url: str, index: int) -> str:
    safe_provider = "".join(ch for ch in provider.lower() if ch.isalnum() or ch == "_") or "source"
    return f"EXT-{safe_provider}-{index + 1:03d}"


def _normalize_external_item(provider: str, item: dict[str, Any], index: int) -> dict[str, Any]:
    url = str(item.get("url") or item.get("link") or item.get("source_url") or "").strip()
    title = str(item.get("title") or item.get("name") or url or f"{provider} result {index + 1}").strip()
    snippet = str(
        item.get("text")
        or item.get("content")
        or item.get("snippet")
        or item.get("description")
        or item.get("summary")
        or ""
    ).strip()
    score = item.get("score") if item.get("score") is not None else item.get("confidence")
    return {
        "source_id": _source_id(provider, url, index),
        "provider": provider,
        "title": title[:240],
        "url": url[:500],
        "snippet": snippet[:800],
        "published_date": item.get("publishedDate") or item.get("published_date") or item.get("date"),
        "score": score,
    }


def _exa_search(client: httpx.Client, *, query: str, max_results: int, timeout: float) -> list[dict[str, Any]]:
    api_key = _env_value(PROVIDER_ENV["exa"])
    response = client.post(
        "https://api.exa.ai/search",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "query": query,
            "numResults": max_results,
            "contents": {"text": True, "highlights": True},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    raw_results = payload.get("results") if isinstance(payload, dict) else []
    return [
        _normalize_external_item("exa", item, index)
        for index, item in enumerate(raw_results if isinstance(raw_results, list) else [])
        if isinstance(item, dict)
    ]


def _tavily_search(client: httpx.Client, *, query: str, max_results: int, timeout: float) -> list[dict[str, Any]]:
    api_key = _env_value(PROVIDER_ENV["tavily"])
    response = client.post(
        "https://api.tavily.com/search",
        json={
            "api_key": api_key,
            "query": query,
            "search_depth": "advanced",
            "max_results": max_results,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    raw_results = payload.get("results") if isinstance(payload, dict) else []
    return [
        _normalize_external_item("tavily", item, index)
        for index, item in enumerate(raw_results if isinstance(raw_results, list) else [])
        if isinstance(item, dict)
    ]


def _load_qcc_config() -> dict[str, Any]:
    raw_json = os.getenv("SIQ_QCC_MCP_CONFIG_JSON")
    if raw_json:
        payload = json.loads(raw_json)
        return payload if isinstance(payload, dict) else {}
    config_path = _env_value(("SIQ_QCC_MCP_CONFIG_PATH", "QCC_MCP_CONFIG_PATH"))
    if not config_path:
        return {}
    path = Path(config_path).expanduser()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _sse_json_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        raw = stripped[5:].strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _qcc_search(client: httpx.Client, *, query: str, timeout: float) -> list[dict[str, Any]]:
    config = _load_qcc_config()
    servers = config.get("mcpServers") if isinstance(config.get("mcpServers"), dict) else {}
    server = servers.get("qcc-company") if isinstance(servers, dict) else None
    if not isinstance(server, dict) or not server.get("url"):
        raise ValueError("qcc_mcp_server_not_configured")
    url = str(server["url"])
    headers = {
        **(server.get("headers") if isinstance(server.get("headers"), dict) else {}),
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "get_company_registration_info",
            "arguments": {"searchKey": query},
        },
    }
    response = client.post(url, headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    events = _sse_json_events(response.text)
    data: Any = {}
    for event in events:
        result = event.get("result")
        if not isinstance(result, dict):
            continue
        content = result.get("content")
        if isinstance(content, list) and content:
            text = content[0].get("text") if isinstance(content[0], dict) else None
            if isinstance(text, str):
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = {"raw_text": text}
                break
    if not data:
        return []
    return [_normalize_external_item("qcc", {"title": query, "content": json.dumps(data, ensure_ascii=False)}, 0)]


def _run_provider(
    provider: str,
    *,
    query: str,
    max_results: int,
    timeout: float,
    client: httpx.Client,
) -> dict[str, Any]:
    status = _provider_status(provider)
    if not status.get("configured"):
        return _empty_provider_result(provider, status="skipped", reason="provider_not_configured")
    try:
        if provider == "exa":
            results = _exa_search(client, query=query, max_results=max_results, timeout=timeout)
        elif provider == "tavily":
            results = _tavily_search(client, query=query, max_results=max_results, timeout=timeout)
        elif provider == "qcc":
            results = _qcc_search(client, query=query, timeout=timeout)
        else:
            return _empty_provider_result(provider, status="skipped", reason="unknown_provider")
    except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
        payload = _empty_provider_result(provider, status="error", reason="provider_request_failed")
        payload["error"] = str(exc)[:300]
        return payload
    return {
        "schema_version": EXTERNAL_PROVIDER_RESULT_SCHEMA,
        "provider": provider,
        "status": "completed",
        "reason": None,
        "results": results[:max_results],
        "result_count": len(results[:max_results]),
        "error": None,
    }


def run_external_research(
    *,
    query: str | None,
    providers: list[str] | tuple[str, ...] | None = None,
    max_results: int | str | None = 5,
    enabled: bool = False,
    timeout: float = 10.0,
) -> dict[str, Any]:
    normalized_query = _normalize_query(query)
    normalized_providers = tuple(
        str(provider).strip().lower()
        for provider in (providers or DEFAULT_PROVIDERS)
        if str(provider or "").strip()
    )
    if not enabled:
        provider_results = [
            _empty_provider_result(provider, status="skipped", reason="external_research_disabled")
            for provider in normalized_providers
        ]
        return {
            "schema_version": EXTERNAL_RESEARCH_SCHEMA,
            "enabled": False,
            "query": normalized_query,
            "providers": provider_results,
            "results": [],
            "result_count": 0,
        }

    limit = _normalize_max_results(max_results)
    provider_results: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    with httpx.Client() as client:
        for provider in normalized_providers:
            provider_payload = _run_provider(
                provider,
                query=normalized_query,
                max_results=limit,
                timeout=float(timeout),
                client=client,
            )
            provider_results.append(provider_payload)
            for item in provider_payload.get("results") or []:
                if isinstance(item, dict):
                    results.append(item)

    return {
        "schema_version": EXTERNAL_RESEARCH_SCHEMA,
        "enabled": True,
        "query": normalized_query,
        "providers": provider_results,
        "results": results[:limit],
        "result_count": len(results[:limit]),
    }
