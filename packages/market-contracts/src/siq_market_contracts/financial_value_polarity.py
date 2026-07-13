from __future__ import annotations

from typing import Any, Literal

FINANCIAL_VALUE_POLARITY_CONTRACT_VERSION = "siq_financial_value_polarity_v1"

CanonicalValuePolarity = Literal["signed", "deduction_magnitude"]

_DEDUCTION_MAGNITUDE_CANONICALS_BY_MARKET = {
    "EU": frozenset({"cost_of_sales", "finance_costs", "income_tax_expense"}),
    "HK": frozenset({"cost_of_sales", "finance_costs", "income_tax_expense"}),
}


def canonical_value_polarity(market: Any, canonical_name: Any) -> CanonicalValuePolarity:
    """Return the signed-value contract for one market canonical.

    HK and EU PDF extractors publish deduction rows as positive magnitudes even
    when a statement presents them in parentheses. Every undeclared canonical
    remains signed so that a real sign error cannot be hidden by absolute-value
    comparison.
    """
    market_value = getattr(market, "value", market)
    market_token = str(market_value or "").strip().upper()
    canonical_token = str(canonical_name or "").strip().lower()
    if canonical_token in _DEDUCTION_MAGNITUDE_CANONICALS_BY_MARKET.get(market_token, ()):
        return "deduction_magnitude"
    return "signed"


__all__ = [
    "FINANCIAL_VALUE_POLARITY_CONTRACT_VERSION",
    "CanonicalValuePolarity",
    "canonical_value_polarity",
]
