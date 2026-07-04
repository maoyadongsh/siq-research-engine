#!/usr/bin/env python3
from __future__ import annotations

import os
from typing import Optional

LOCAL_EMBEDDING_BASE_URL = os.getenv("SIQ_EMBEDDING_BASE_URL", "http://127.0.0.1:8000/v1")
LOCAL_EMBEDDING_MODEL = os.getenv("SIQ_EMBEDDING_MODEL", "Qwen3-VL-Embedding-2B")
LOCAL_EMBEDDING_DIMENSIONS = int(os.getenv("SIQ_EMBEDDING_DIMENSIONS", "1024"))

PHYSICAL_SHARED_COLLECTION = "ic_collaboration_shared"

COLLECTION_ALIASES = {
    "siq_deal_shared": PHYSICAL_SHARED_COLLECTION,
    "siq_ic_chairman": "ic_chairman",
    "siq_ic_finance_auditor": "ic_finance_auditor",
    "siq_ic_legal_scanner": "ic_legal_scanner",
    "siq_ic_risk_controller": "ic_risk_controller",
    "siq_ic_sector_expert": "ic_sector_expert",
    "siq_ic_strategist": "ic_strategist",
    "siq_ic_master_coordinator": "ic_master_coordinator",
}


def normalize_collection_name(name: Optional[str]) -> str:
    value = str(name or "").strip()
    return COLLECTION_ALIASES.get(value, value)


def build_local_openai_client(base_url: Optional[str] = None, timeout_seconds: float = 60.0) -> OpenAI:
    import httpx
    from openai import OpenAI

    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
    http_client = httpx.Client(timeout=timeout, trust_env=False)
    return OpenAI(
        api_key="EMPTY",
        base_url=base_url or LOCAL_EMBEDDING_BASE_URL,
        http_client=http_client,
    )
