"""API-level constants and stable errors for the Research Universe."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AgentType = Literal["analysis", "factcheck", "tracking", "legal"]
ArtifactType = Literal["analysis", "factcheck", "tracking"]

RESEARCH_MARKET_ORDER = ("CN", "HK", "US", "EU", "KR", "JP")
RESEARCH_MARKET_METADATA = {
    "CN": {"label": "中国内地市场", "order": 1},
    "HK": {"label": "香港市场", "order": 2},
    "US": {"label": "美国市场", "order": 3},
    "EU": {"label": "欧洲市场", "order": 4},
    "KR": {"label": "韩国市场", "order": 5},
    "JP": {"label": "日本市场", "order": 6},
}
AGENT_TYPES = frozenset({"analysis", "factcheck", "tracking", "legal"})
ARTIFACT_TYPES = frozenset({"analysis", "factcheck", "tracking"})


@dataclass
class ResearchUniverseError(Exception):
    code: str
    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def normalize_market(value: str) -> str:
    market = str(value or "").strip().upper().replace("-", "_")
    if market == "US_SEC":
        market = "US"
    if market not in RESEARCH_MARKET_ORDER:
        raise ResearchUniverseError("market_not_supported", "The requested market is not supported.", 404)
    return market


def normalize_agent_type(value: str) -> str:
    agent_type = str(value or "").strip().lower()
    if agent_type not in AGENT_TYPES:
        raise ResearchUniverseError("agent_type_not_supported", "The requested agent type is not supported.", 400)
    return agent_type


def normalize_artifact_type(value: str) -> str:
    artifact_type = str(value or "").strip().lower()
    if artifact_type not in ARTIFACT_TYPES:
        raise ResearchUniverseError("artifact_type_not_supported", "The requested artifact type is not supported.", 400)
    return artifact_type


__all__ = [
    "AGENT_TYPES",
    "ARTIFACT_TYPES",
    "AgentType",
    "ArtifactType",
    "RESEARCH_MARKET_METADATA",
    "RESEARCH_MARKET_ORDER",
    "ResearchUniverseError",
    "normalize_agent_type",
    "normalize_artifact_type",
    "normalize_market",
]
