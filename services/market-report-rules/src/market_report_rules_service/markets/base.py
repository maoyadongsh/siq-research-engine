from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import Market, RuleProfile, StatementType


@dataclass(frozen=True)
class MetricRule:
    canonical_name: str
    statement_type: StatementType
    labels: tuple[str, ...]
    priority: int = 100


@dataclass(frozen=True)
class MarketFeaturePage:
    page_id: str
    title: str
    owner: str
    route_hint: str
    service_path: str
    status: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketStorageProfile:
    market: Market
    postgres_database: str
    postgres_schema: str
    wiki_namespace: str
    raw_download_root: str
    parsed_artifact_root: str
    agent_policy: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketModule:
    market: Market
    code: str
    display_name: str
    rule_profile: RuleProfile
    storage_profile: MarketStorageProfile
    rule_count: int
    parser_boundary: str
    feature_pages: tuple[MarketFeaturePage, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market.value,
            "code": self.code,
            "display_name": self.display_name,
            "rule_profile_id": self.rule_profile.profile_id,
            "rule_version": self.rule_profile.rule_version,
            "rule_count": self.rule_count,
            "parser_boundary": self.parser_boundary,
            "feature_pages": [
                {
                    "page_id": page.page_id,
                    "title": page.title,
                    "owner": page.owner,
                    "route_hint": page.route_hint,
                    "service_path": page.service_path,
                    "status": page.status,
                    "notes": list(page.notes),
                }
                for page in self.feature_pages
            ],
            "storage_profile": {
                "postgres_database": self.storage_profile.postgres_database,
                "postgres_schema": self.storage_profile.postgres_schema,
                "wiki_namespace": self.storage_profile.wiki_namespace,
                "raw_download_root": self.storage_profile.raw_download_root,
                "parsed_artifact_root": self.storage_profile.parsed_artifact_root,
                "agent_policy": self.storage_profile.agent_policy,
                "notes": list(self.storage_profile.notes),
            },
            "notes": list(self.notes),
        }
