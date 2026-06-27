from __future__ import annotations

from ..models import Market, RuleProfile
from .base import MarketModule, MarketStorageProfile
from .cn.definition import MARKET_MODULE as CN_MODULE
from .eu.definition import MARKET_MODULE as EU_MODULE
from .hk.definition import MARKET_MODULE as HK_MODULE
from .jp.definition import MARKET_MODULE as JP_MODULE
from .kr.definition import MARKET_MODULE as KR_MODULE
from .us.definition import MARKET_MODULE as US_MODULE


MARKET_MODULES: dict[Market, MarketModule] = {
    Market.CN: CN_MODULE,
    Market.HK: HK_MODULE,
    Market.US: US_MODULE,
    Market.JP: JP_MODULE,
    Market.KR: KR_MODULE,
    Market.EU: EU_MODULE,
}


def get_market_module(market: Market | str) -> MarketModule:
    return MARKET_MODULES[Market(market)]


def list_market_modules() -> list[MarketModule]:
    return [MARKET_MODULES[market] for market in (Market.CN, Market.HK, Market.US, Market.JP, Market.KR, Market.EU)]


def get_market_profile(market: Market | str) -> RuleProfile:
    return get_market_module(market).rule_profile


def list_market_profiles() -> list[RuleProfile]:
    return [module.rule_profile for module in list_market_modules()]


def get_market_storage_profile(market: Market | str) -> MarketStorageProfile:
    return get_market_module(market).storage_profile


def list_market_storage_profiles() -> list[MarketStorageProfile]:
    return [module.storage_profile for module in list_market_modules()]
