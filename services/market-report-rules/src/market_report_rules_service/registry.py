from __future__ import annotations

from .markets import get_market_profile, list_market_profiles
from .models import Market, RuleProfile


def get_profile(market: Market | str) -> RuleProfile:
    return get_market_profile(market)


def list_profiles() -> list[RuleProfile]:
    return list_market_profiles()
