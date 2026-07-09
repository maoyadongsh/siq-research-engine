from __future__ import annotations

from typing import Any

from .base import MarketDocumentFullContext, MarketDocumentFullRows
from .market_specific import apply_jp_rules
from .registry import GenericDelegatingRule


class JPDocumentFullRule(GenericDelegatingRule):
    market = "JP"

    def build_rows(self, document_full: dict[str, Any], context: MarketDocumentFullContext) -> MarketDocumentFullRows:
        return apply_jp_rules(super().build_rows(document_full, context))
