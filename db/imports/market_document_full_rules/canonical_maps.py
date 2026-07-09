from __future__ import annotations

import re
from typing import Any

from .common import canonical_common_name


MARKET_ALIASES: dict[str, dict[str, set[str]]] = {
    "HK": {
        "revenue": {"收益", "收入", "turnover", "revenue"},
        "profit_attributable_to_owners": {"公司權益持有人應佔盈利", "profit attributable to equity holders", "profit attributable to owners"},
        "total_assets": {"總資產", "total assets"},
        "total_liabilities": {"總負債", "total liabilities"},
        "total_equity": {"權益總額", "total equity"},
        "basic_eps": {"每股基本盈利", "basic earnings per share"},
    },
    "JP": {
        "revenue": {"売上収益", "売上高", "営業収益"},
        "operating_profit": {"営業利益"},
        "profit_attributable_to_owners": {"親会社の所有者に帰属する当期利益", "親会社株主に帰属する当期純利益"},
        "total_assets": {"資産合計", "総資産"},
        "total_liabilities": {"負債合計"},
        "total_equity": {"資本合計", "純資産合計"},
        "basic_eps": {"基本的1株当たり当期利益", "1株当たり当期純利益"},
    },
    "KR": {
        "revenue": {"매출액", "수익"},
        "operating_profit": {"영업이익"},
        "net_profit": {"당기순이익"},
        "profit_attributable_to_owners": {"지배기업소유주지분순이익", "지배기업의 소유주에게 귀속되는 당기순이익"},
        "total_assets": {"자산총계"},
        "total_liabilities": {"부채총계"},
        "total_equity": {"자본총계"},
        "basic_eps": {"기본주당이익"},
    },
    "EU": {
        "revenue": {"ifrs-full:revenue", "ifrs-full:revenuefromcontractswithcustomers", "revenue"},
        "operating_profit": {"ifrs-full:profitlossfromoperatingactivities", "operating profit"},
        "net_profit": {"ifrs-full:profitloss", "profit for the year"},
        "total_assets": {"ifrs-full:assets"},
        "total_liabilities": {"ifrs-full:liabilities"},
        "total_equity": {"ifrs-full:equity"},
        "operating_cash_flow": {"ifrs-full:cashflowsfromusedinoperatingactivities", "ifrs-full:netcashflowsfromusedinoperatingactivities"},
        "basic_eps": {"ifrs-full:basicearningslosspershare"},
    },
    "US": {
        "revenue": {"us-gaap:revenues", "us-gaap:salesrevenuenet", "us-gaap:revenuefromcontractwithcustomerexcludingassessedtax"},
        "operating_profit": {"us-gaap:operatingincomeloss"},
        "net_profit": {"us-gaap:netincomeloss"},
        "total_assets": {"us-gaap:assets"},
        "total_liabilities": {"us-gaap:liabilities"},
        "total_equity": {"us-gaap:stockholdersequity"},
        "basic_eps": {"us-gaap:earningspersharebasic"},
        "diluted_eps": {"us-gaap:earningspersharediluted"},
    },
}


INDUSTRY_HINTS = {
    "bank": {"net_interest_income", "loans_and_advances", "customer_deposits", "interest income", "interest expense"},
    "insurance": {"insurance revenue", "insurance service result", "premiums", "claims"},
    "internet": {"monthly active users", "annual active users", "gmv", "paying users", "subscribers"},
    "energy": {"production volume", "proved reserves", "barrels", "mwh", "installed capacity"},
    "retail": {"same-store sales", "stores", "gross merchandise value"},
}


def _key(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def resolve_canonical(
    market: str,
    *names: Any,
    industry_profile: str | None = None,
) -> tuple[str | None, str]:
    canonical, scope = canonical_common_name(*names)
    if scope == "common_core":
        return canonical, scope

    haystack = {_key(name) for name in names if str(name or "").strip()}
    for metric, aliases in MARKET_ALIASES.get(market.upper(), {}).items():
        if any(_key(alias) in haystack for alias in aliases):
            return metric, "market" if market.upper() != "EU" else "country"

    joined = " ".join(str(name or "").lower() for name in names)
    for industry, hints in INDUSTRY_HINTS.items():
        if industry_profile and industry in str(industry_profile).lower():
            if any(hint.lower() in joined for hint in hints):
                return _safe_metric_name(next(iter([name for name in names if name])), fallback="industry_metric"), "industry"

    if canonical:
        return canonical, scope
    return None, "unmapped"


def _safe_metric_name(value: Any, *, fallback: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return safe or fallback
