from __future__ import annotations

from typing import Any

from .base import MarketDocumentFullContext, MarketDocumentFullRows
from .market_specific import apply_kr_rules
from .registry import GenericDelegatingRule


class KRDocumentFullRule(GenericDelegatingRule):
    market = "KR"

    def build_rows(self, document_full: dict[str, Any], context: MarketDocumentFullContext) -> MarketDocumentFullRows:
        return apply_kr_rules(super().build_rows(document_full, context))
