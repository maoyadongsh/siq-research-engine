from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import MarketDocumentFullContext, MarketDocumentFullRows
from .market_specific import apply_us_sec_rules
from .registry import GenericDelegatingRule


class USSecDocumentFullRule(GenericDelegatingRule):
    market = "US"

    def detect(self, document_full: dict[str, Any], path: Path) -> bool:
        filing = document_full.get("filing") if isinstance(document_full.get("filing"), dict) else {}
        if str(filing.get("market") or "").strip().upper() == "US":
            return True
        return "US-SEC" in str(path).upper() or super().detect(document_full, path)

    def build_rows(self, document_full: dict[str, Any], context: MarketDocumentFullContext) -> MarketDocumentFullRows:
        return apply_us_sec_rules(super().build_rows(document_full, context))
