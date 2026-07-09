from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import MarketDocumentFullContext, MarketDocumentFullRows, MarketDocumentFullRule

MARKET_ALIASES = {"US_SEC": "US", "US-SEC": "US", "US SEC": "US"}


def normalize_market(value: str) -> str:
    market = str(value or "").strip().upper()
    return MARKET_ALIASES.get(market, market)


class GenericDelegatingRule(MarketDocumentFullRule):
    market: str = ""

    def detect(self, document_full: dict[str, Any], path: Path) -> bool:
        for source in (
            document_full.get("metadata") if isinstance(document_full.get("metadata"), dict) else {},
            document_full.get("financial_data") if isinstance(document_full.get("financial_data"), dict) else {},
            document_full.get("filing") if isinstance(document_full.get("filing"), dict) else {},
            document_full.get("task", {}).get("submit_config")
            if isinstance(document_full.get("task"), dict) and isinstance(document_full.get("task", {}).get("submit_config"), dict)
            else {},
        ):
            if normalize_market(str(source.get("market") or "")) == self.market:
                return True
        return f"/{self.market}/" in str(path).upper() or f"_{self.market}_" in str(path).upper()

    def build_rows(self, document_full: dict[str, Any], context: MarketDocumentFullContext) -> MarketDocumentFullRows:
        from .generic import build_generic_rows

        return build_generic_rows(self.market, document_full, context)


def rule_for_market(market: str) -> MarketDocumentFullRule:
    market_code = normalize_market(market)
    if market_code == "HK":
        from .hk import HKDocumentFullRule

        return HKDocumentFullRule()
    if market_code == "JP":
        from .jp import JPDocumentFullRule

        return JPDocumentFullRule()
    if market_code == "KR":
        from .kr import KRDocumentFullRule

        return KRDocumentFullRule()
    if market_code == "EU":
        from .eu import EUDocumentFullRule

        return EUDocumentFullRule()
    if market_code == "US":
        from .us_sec import USSecDocumentFullRule

        return USSecDocumentFullRule()
    raise ValueError(f"Unsupported market: {market}")
