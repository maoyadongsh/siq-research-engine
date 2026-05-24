import asyncio
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from services.hermes_client import HERMES_PROFILES
from services.llm_settings import load_llm_settings


WIKI_ROOT = os.environ.get("WIKI_ROOT", "/home/maoyd/wiki")
COMPANIES_DIR = Path(WIKI_ROOT) / "companies"


def _env_url(name: str, fallback: str) -> str:
    return os.environ.get(name, fallback).strip()


def _hermes_health_url(runs_url: str) -> str:
    parsed = urlparse(runs_url)
    if not parsed.scheme or not parsed.netloc:
        return runs_url
    return f"{parsed.scheme}://{parsed.netloc}/health"


async def _probe_service(
    client: httpx.AsyncClient,
    *,
    service_id: str,
    name: str,
    category: str,
    url: str,
    required: bool = True,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = await client.get(url)
        latency_ms = int((time.perf_counter() - started) * 1000)
        ok = 200 <= response.status_code < 400
        detail: Any
        try:
            detail = response.json()
        except ValueError:
            detail = response.text[:300]
        return {
            "id": service_id,
            "name": name,
            "category": category,
            "url": url,
            "required": required,
            "ok": ok,
            "statusCode": response.status_code,
            "latencyMs": latency_ms,
            "detail": detail,
        }
    except Exception as exc:  # noqa: BLE001 - surface health errors to the UI
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "id": service_id,
            "name": name,
            "category": category,
            "url": url,
            "required": required,
            "ok": False,
            "statusCode": None,
            "latencyMs": latency_ms,
            "detail": str(exc)[:300],
        }


def _wiki_status() -> dict[str, Any]:
    exists = COMPANIES_DIR.is_dir()
    company_count = 0
    generated_count = 0
    if exists:
        company_dirs = [item for item in COMPANIES_DIR.iterdir() if item.is_dir()]
        company_count = len(company_dirs)
        for company_dir in company_dirs:
            for result_dir in ("analysis", "factcheck", "tracking", "legal"):
                target = company_dir / result_dir
                if target.is_dir():
                    generated_count += len(list(target.glob("*.html")))

    return {
        "root": str(Path(WIKI_ROOT).expanduser()),
        "companiesDir": str(COMPANIES_DIR),
        "exists": exists,
        "companyCount": company_count,
        "generatedResultCount": generated_count,
    }


def _model_status() -> dict[str, Any]:
    settings = load_llm_settings(include_secrets=False)
    active = settings.get("activeProvider", "local")
    providers = settings.get("providers", {})
    active_provider = providers.get(active, {})
    return {
        "activeProvider": active,
        "activeProviderName": active_provider.get("providerName") or "",
        "activeModel": active_provider.get("model") or "",
        "activeBaseUrl": active_provider.get("baseUrl") or "",
        "providers": providers,
        "note": "当前模型设置用于 OpenAI-compatible 连接测试；业务 Agent 仍通过 Hermes profiles 调用。",
    }


async def collect_system_status() -> dict[str, Any]:
    service_specs = [
        {
            "service_id": "report_finder",
            "name": "PDF 下载服务",
            "category": "reports",
            "url": _env_url("REPORT_FINDER_HEALTH_URL", "http://127.0.0.1:8000/health"),
        },
        {
            "service_id": "pdf_parser",
            "name": "PDF 解析服务",
            "category": "pdf",
            "url": _env_url("PDF2MD_HEALTH_URL", "http://127.0.0.1:5000/api/health"),
        },
    ]

    for profile, config in HERMES_PROFILES.items():
        display_name = "Hermes finsight_assistant" if profile == "finsight_assistant" else f"Hermes {profile}"
        service_specs.append(
            {
                "service_id": f"hermes_{profile}",
                "name": display_name,
                "category": "agent",
                "url": _env_url(
                    f"HERMES_{profile.upper()}_HEALTH_URL",
                    _hermes_health_url(config["base"]),
                ),
                "required": profile != "finsight_assistant",
            }
        )

    async with httpx.AsyncClient(timeout=2.5) as client:
        services = await asyncio.gather(
            *[
                _probe_service(
                    client,
                    service_id=spec["service_id"],
                    name=spec["name"],
                    category=spec["category"],
                    url=spec["url"],
                    required=spec.get("required", True),
                )
                for spec in service_specs
            ]
        )

    required_services = [service for service in services if service["required"]]
    required_ok = all(service["ok"] for service in required_services)
    wiki = _wiki_status()
    status = "ok" if required_ok and wiki["exists"] else "degraded"

    return {
        "status": status,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "wiki": wiki,
        "model": _model_status(),
        "services": services,
    }
