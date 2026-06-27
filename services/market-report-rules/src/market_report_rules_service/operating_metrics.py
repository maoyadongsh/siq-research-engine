from __future__ import annotations

from dataclasses import dataclass

from .models import Market
from .normalization import compact_label


@dataclass(frozen=True)
class OperatingMetricRule:
    canonical_name: str
    profile: str
    unit_kind: str
    aliases: tuple[str, ...]
    source_candidates: tuple[str, ...]
    validation: tuple[str, ...]
    markets: tuple[Market, ...] = (Market.US, Market.HK)


OPERATING_METRIC_RULES: tuple[OperatingMetricRule, ...] = (
    OperatingMetricRule(
        "active_customers",
        "general",
        "count",
        ("active customers", "customers", "active clients", "活跃客户", "活躍客戶", "客户数", "客戶數"),
        ("MD&A KPI table", "business review", "segment note"),
        ("non_negative", "period_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "employees",
        "general",
        "count",
        ("employees", "headcount", "员工人数", "僱員人數", "雇员人数"),
        ("annual report workforce section", "ESG section"),
        ("non_negative", "integer_like", "evidence_required"),
    ),
    OperatingMetricRule(
        "monthly_active_users",
        "internet_platform",
        "count",
        ("monthly active users", "maus", "mau", "月活跃用户", "月活躍用戶"),
        ("MD&A KPI table", "operating highlights"),
        ("non_negative", "period_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "daily_active_users",
        "internet_platform",
        "count",
        ("daily active users", "daus", "dau", "日活跃用户", "日活躍用戶"),
        ("MD&A KPI table", "operating highlights"),
        ("non_negative", "period_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "gmv",
        "internet_platform",
        "money",
        ("gross merchandise value", "gmv", "商品交易总额", "商品交易總額"),
        ("MD&A KPI table", "segment operating data"),
        ("non_negative", "currency_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "paid_subscribers",
        "saas",
        "count",
        ("paid subscribers", "paying customers", "paid customers", "付费客户", "付費客戶"),
        ("MD&A KPI table", "10-K business section"),
        ("non_negative", "period_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "arr",
        "saas",
        "money",
        ("annual recurring revenue", "arr", "年度经常性收入", "年度經常性收入"),
        ("MD&A KPI table", "shareholder letter"),
        ("non_negative", "currency_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "net_revenue_retention",
        "saas",
        "ratio_percent",
        ("net revenue retention", "nrr", "dollar-based net retention", "净收入留存率", "淨收入留存率"),
        ("MD&A KPI table", "10-K business section"),
        ("ratio_reasonable", "evidence_required"),
    ),
    OperatingMetricRule(
        "stores_count",
        "retail",
        "count",
        ("stores", "number of stores", "店铺数", "門店數", "门店数"),
        ("store network table", "operating review"),
        ("non_negative", "integer_like", "evidence_required"),
    ),
    OperatingMetricRule(
        "same_store_sales_growth",
        "retail",
        "ratio_percent",
        ("same-store sales growth", "comparable sales growth", "同店销售增长", "同店銷售增長"),
        ("MD&A KPI table", "operating review"),
        ("growth_reasonable", "evidence_required"),
    ),
    OperatingMetricRule(
        "production_volume",
        "manufacturing",
        "quantity",
        ("production volume", "output", "产量", "產量"),
        ("production table", "business review"),
        ("non_negative", "unit_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "shipments",
        "manufacturing",
        "quantity",
        ("shipments", "delivery volume", "出货量", "出貨量", "交付量"),
        ("business review", "operating highlights"),
        ("non_negative", "unit_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "loan_balance",
        "bank",
        "money",
        ("loans and advances", "gross loans", "customer loans", "客户贷款", "客戶貸款"),
        ("bank balance sheet", "financial review"),
        ("non_negative", "currency_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "deposits",
        "bank",
        "money",
        ("customer deposits", "deposits from customers", "客户存款", "客戶存款"),
        ("bank balance sheet", "financial review"),
        ("non_negative", "currency_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "net_interest_margin",
        "bank",
        "ratio_percent",
        ("net interest margin", "nim", "净息差", "淨息差"),
        ("bank KPI table", "financial review"),
        ("ratio_reasonable", "evidence_required"),
    ),
    OperatingMetricRule(
        "npl_ratio",
        "bank",
        "ratio_percent",
        ("non-performing loan ratio", "npl ratio", "不良贷款率", "不良貸款率"),
        ("bank asset quality table", "risk management section"),
        ("ratio_reasonable", "evidence_required"),
    ),
    OperatingMetricRule(
        "gross_written_premiums",
        "insurance",
        "money",
        ("gross written premiums", "gwp", "gross premiums written", "总保费", "總保費"),
        ("insurance KPI table", "business review"),
        ("non_negative", "currency_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "combined_ratio",
        "insurance",
        "ratio_percent",
        ("combined ratio", "综合成本率", "綜合成本率"),
        ("insurance KPI table", "business review"),
        ("ratio_reasonable", "evidence_required"),
    ),
    OperatingMetricRule(
        "contracted_sales",
        "real_estate",
        "money",
        ("contracted sales", "contract sales", "合约销售", "合約銷售"),
        ("property operating data table", "business review"),
        ("non_negative", "currency_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "gross_floor_area",
        "real_estate",
        "area",
        ("gross floor area", "gfa", "建筑面积", "建築面積"),
        ("property project table", "business review"),
        ("non_negative", "unit_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "proved_reserves",
        "energy",
        "quantity",
        ("proved reserves", "reserves", "探明储量", "探明儲量"),
        ("reserve table", "supplementary oil and gas disclosure"),
        ("non_negative", "unit_required", "evidence_required"),
    ),
    OperatingMetricRule(
        "production_per_day",
        "energy",
        "quantity",
        ("production per day", "average daily production", "日产量", "日產量"),
        ("production table", "business review"),
        ("non_negative", "unit_required", "evidence_required"),
    ),
)


OPERATING_RULE_BY_ALIAS = {
    compact_label(alias): rule
    for rule in OPERATING_METRIC_RULES
    for alias in rule.aliases
}


def find_operating_metric_rule(label: str, market: Market, industry_profile: str = "general") -> OperatingMetricRule | None:
    normalized = compact_label(label)
    if not normalized:
        return None
    direct = OPERATING_RULE_BY_ALIAS.get(normalized)
    if direct and market in direct.markets and _profile_allowed(direct.profile, industry_profile):
        return direct
    candidates = [
        (alias, rule)
        for alias, rule in OPERATING_RULE_BY_ALIAS.items()
        if market in rule.markets
        and _profile_allowed(rule.profile, industry_profile)
        and alias
        and (alias in normalized or normalized in alias)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: (_profile_rank(item[1].profile, industry_profile), len(item[0])))[1]


def _profile_allowed(rule_profile: str, requested_profile: str) -> bool:
    requested = (requested_profile or "general").strip().lower()
    return rule_profile == "general" or rule_profile == requested


def _profile_rank(rule_profile: str, requested_profile: str) -> int:
    return 0 if rule_profile == (requested_profile or "general").strip().lower() else 1


def list_operating_metric_rules() -> list[dict[str, object]]:
    return [
        {
            "canonical_name": rule.canonical_name,
            "profile": rule.profile,
            "unit_kind": rule.unit_kind,
            "aliases": list(rule.aliases),
            "source_candidates": list(rule.source_candidates),
            "validation": list(rule.validation),
            "markets": [market.value for market in rule.markets],
        }
        for rule in OPERATING_METRIC_RULES
    ]
