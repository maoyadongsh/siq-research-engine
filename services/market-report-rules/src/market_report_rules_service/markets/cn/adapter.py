from __future__ import annotations

import os
from typing import Any

CN_LEGACY_RULE_VERSION = "financial_rules_v14"
CN_REPORT_FINDER_BASE = (
    os.environ.get("SIQ_CN_REPORT_FINDER_BASE")
    or os.environ.get("SIQ_REPORT_FINDER_BASE")
    or os.environ.get("REPORT_FINDER_BASE")
    or "http://127.0.0.1:18000"
).rstrip("/")
CN_PDF2MD_API_BASE = (
    os.environ.get("SIQ_CN_PDF2MD_API_BASE")
    or os.environ.get("SIQ_PDF2MD_API_BASE")
    or os.environ.get("PDF2MD_API_BASE")
    or "http://127.0.0.1:15000"
).rstrip("/")
CN_PDF2MD_ACCESS_TOKEN = (
    os.environ.get("SIQ_CN_PDF2MD_ACCESS_TOKEN")
    or os.environ.get("SIQ_PDF2MD_ACCESS_TOKEN")
    or os.environ.get("PDF2MD_ACCESS_TOKEN")
    or ""
).strip()


def cn_legacy_entrypoints() -> dict[str, Any]:
    return {
        "rule_version": CN_LEGACY_RULE_VERSION,
        "download_service": {
            "path": "/home/maoyd/siq-research-engine/services/market-report-finder",
            "module": "market_report_finder_service.app:app",
            "page": "/",
            "base_url": CN_REPORT_FINDER_BASE,
        },
        "pdf_parser": {
            "path": "/home/maoyd/siq-research-engine/apps/pdf-parser",
            "module": "app:app",
            "page": "/",
            "financial_extractor": "financial_extractor.py",
            "base_url": CN_PDF2MD_API_BASE,
        },
        "integration_mode": "market_adapter_proxy",
        "front_end_compatibility": {
            "unchanged_paths": ["/api/v1/*", "/api/pdf/*", "/api/downloads/*"],
            "note": "Existing frontend routes keep working through the API aggregation layer.",
        },
    }


def cn_pdf2md_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = dict(extra or {})
    if CN_PDF2MD_ACCESS_TOKEN:
        headers.setdefault("X-PDF2MD-Token", CN_PDF2MD_ACCESS_TOKEN)
    return headers
