from __future__ import annotations

from typing import Any

from .markets import get_market_storage_profile, list_market_storage_profiles
from .markets.base import MarketStorageProfile
from .models import Market


def get_storage_profile(market: Market | str) -> MarketStorageProfile:
    return get_market_storage_profile(market)


def report_bucket(report_type: str | None, report_form: str | None = None) -> str:
    text = f"{report_type or ''} {report_form or ''}".lower()
    annual_tokens = ("annual", "year", "10-k", "20-f", "年报", "年度")
    return "年报" if any(token in text for token in annual_tokens) else "财报"


def artifact_file_layout(
    *,
    market: Market,
    company_name: str | None,
    ticker: str,
    report_type: str | None,
    report_form: str | None,
    artifact_id: str,
) -> dict[str, Any]:
    profile = get_storage_profile(market)
    company_dir = (company_name or ticker or "unknown").strip() or "unknown"
    bucket = report_bucket(report_type, report_form)
    return {
        "raw_download_dir": f"{profile.raw_download_root}/{company_dir}/{bucket}",
        "parsed_artifact_dir": f"{profile.parsed_artifact_root}/{company_dir}/{bucket}/{artifact_id}",
        "bucket": bucket,
        "filename_contract": "<company>_<market>_<ticker>_<report_end>_<report_type>_<published_at>_<source_id>_<hash>.<ext>",
        "market": market.value,
    }


def list_storage_profiles() -> list[dict[str, Any]]:
    return [
        {
            "market": profile.market.value,
            "postgres_database": profile.postgres_database,
            "postgres_schema": profile.postgres_schema,
            "wiki_namespace": profile.wiki_namespace,
            "raw_download_root": profile.raw_download_root,
            "parsed_artifact_root": profile.parsed_artifact_root,
            "agent_policy": profile.agent_policy,
            "notes": list(profile.notes),
        }
        for profile in list_market_storage_profiles()
    ]
