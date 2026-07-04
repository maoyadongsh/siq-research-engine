from __future__ import annotations

from dataclasses import dataclass

from .models import Market, StatementType


@dataclass(frozen=True)
class IndustryProfile:
    profile_id: str
    label: str
    markets: tuple[Market, ...]
    required_financial_metrics: tuple[str, ...]
    optional_financial_metrics: tuple[str, ...]
    operating_metric_profiles: tuple[str, ...]
    validation_notes: tuple[str, ...]
    statement_overrides: dict[str, str]


INDUSTRY_PROFILES: dict[str, IndustryProfile] = {
    "general": IndustryProfile(
        profile_id="general",
        label="General industrial/commercial issuer",
        markets=(Market.US, Market.HK, Market.EU),
        required_financial_metrics=("operating_revenue", "net_profit", "total_assets", "total_liabilities", "total_equity", "operating_cash_flow_net"),
        optional_financial_metrics=("gross_profit", "operating_profit", "basic_eps", "diluted_eps", "cash_and_cash_equivalents"),
        operating_metric_profiles=("general",),
        validation_notes=("Use standard three-statement bridge checks.",),
        statement_overrides={},
    ),
    "saas": IndustryProfile(
        profile_id="saas",
        label="SaaS/software subscription issuer",
        markets=(Market.US, Market.HK, Market.EU),
        required_financial_metrics=("operating_revenue", "gross_profit", "net_profit", "operating_cash_flow_net"),
        optional_financial_metrics=("deferred_revenue", "remaining_performance_obligations", "sales_and_marketing_expense", "research_and_development_expense"),
        operating_metric_profiles=("general", "saas"),
        validation_notes=("ARR/NRR are usually non-GAAP operating KPIs and need evidence, not pure XBRL reliance.",),
        statement_overrides={},
    ),
    "internet_platform": IndustryProfile(
        profile_id="internet_platform",
        label="Internet platform/e-commerce/gaming issuer",
        markets=(Market.US, Market.HK, Market.EU),
        required_financial_metrics=("operating_revenue", "gross_profit", "net_profit", "operating_cash_flow_net"),
        optional_financial_metrics=("traffic_acquisition_cost", "share_based_compensation", "segment_revenue"),
        operating_metric_profiles=("general", "internet_platform"),
        validation_notes=("MAU/DAU/GMV definitions differ by company and need company-level override support.",),
        statement_overrides={},
    ),
    "retail": IndustryProfile(
        profile_id="retail",
        label="Retail/restaurant/consumer stores issuer",
        markets=(Market.US, Market.HK, Market.EU),
        required_financial_metrics=("operating_revenue", "gross_profit", "net_profit", "inventories", "operating_cash_flow_net"),
        optional_financial_metrics=("same_store_sales_growth", "lease_liabilities", "right_of_use_assets"),
        operating_metric_profiles=("general", "retail"),
        validation_notes=("Store count and same-store sales are usually operating tables, not XBRL-standardized.",),
        statement_overrides={},
    ),
    "manufacturing": IndustryProfile(
        profile_id="manufacturing",
        label="Manufacturing/hardware/auto issuer",
        markets=(Market.US, Market.HK, Market.EU),
        required_financial_metrics=("operating_revenue", "gross_profit", "inventories", "property_plant_equipment", "capital_expenditure", "operating_cash_flow_net"),
        optional_financial_metrics=("production_volume", "shipments", "backlog"),
        operating_metric_profiles=("general", "manufacturing"),
        validation_notes=("Shipment and production units require unit-aware comparisons.",),
        statement_overrides={},
    ),
    "bank": IndustryProfile(
        profile_id="bank",
        label="Banking issuer",
        markets=(Market.US, Market.HK, Market.EU),
        required_financial_metrics=("net_interest_income", "total_assets", "total_liabilities", "total_equity", "loan_balance", "deposits"),
        optional_financial_metrics=("provision_for_credit_losses", "allowance_for_credit_losses", "net_interest_margin", "npl_ratio"),
        operating_metric_profiles=("general", "bank"),
        validation_notes=("Do not require current/non-current split; use bank-specific credit-quality and capital-ratio checks.",),
        statement_overrides={StatementType.INCOME_STATEMENT.value: "bank_income_statement"},
    ),
    "insurance": IndustryProfile(
        profile_id="insurance",
        label="Insurance issuer",
        markets=(Market.US, Market.HK, Market.EU),
        required_financial_metrics=("insurance_revenue", "gross_written_premiums", "net_profit", "total_assets", "total_liabilities", "total_equity"),
        optional_financial_metrics=("combined_ratio", "contractual_service_margin", "investment_income"),
        operating_metric_profiles=("general", "insurance"),
        validation_notes=("IFRS 17/HKFRS 17 changes revenue and liability presentation; use insurance profile rules.",),
        statement_overrides={},
    ),
    "real_estate": IndustryProfile(
        profile_id="real_estate",
        label="Real estate developer/operator",
        markets=(Market.US, Market.HK, Market.EU),
        required_financial_metrics=("operating_revenue", "inventories", "investment_properties", "net_profit", "total_assets", "total_liabilities"),
        optional_financial_metrics=("contracted_sales", "gross_floor_area", "land_bank"),
        operating_metric_profiles=("general", "real_estate"),
        validation_notes=("Fair value gains and contracted sales often need separate evidence and should not be confused with revenue.",),
        statement_overrides={},
    ),
    "energy": IndustryProfile(
        profile_id="energy",
        label="Energy/oil and gas/mining issuer",
        markets=(Market.US, Market.HK, Market.EU),
        required_financial_metrics=("operating_revenue", "depletion_depreciation_amortization", "capital_expenditure", "operating_cash_flow_net"),
        optional_financial_metrics=("proved_reserves", "production_per_day", "lifting_cost"),
        operating_metric_profiles=("general", "energy"),
        validation_notes=("Reserve and production KPIs must preserve unit and commodity type.",),
        statement_overrides={},
    ),
    "pharma": IndustryProfile(
        profile_id="pharma",
        label="Pharmaceuticals and life sciences issuer",
        markets=(Market.EU,),
        required_financial_metrics=("operating_revenue", "net_profit", "total_assets", "total_liabilities", "total_equity", "operating_cash_flow_net"),
        optional_financial_metrics=("research_and_development_expense", "gross_profit", "cash_and_cash_equivalents"),
        operating_metric_profiles=("general",),
        validation_notes=("R&D and pipeline KPIs are operating disclosures and need page/table evidence.",),
        statement_overrides={},
    ),
    "semiconductor": IndustryProfile(
        profile_id="semiconductor",
        label="Semiconductor and semiconductor equipment issuer",
        markets=(Market.HK, Market.EU,),
        required_financial_metrics=("operating_revenue", "gross_profit", "net_profit", "inventories", "property_plant_equipment", "operating_cash_flow_net"),
        optional_financial_metrics=("capital_expenditure", "research_and_development_expense"),
        operating_metric_profiles=("general", "manufacturing"),
        validation_notes=("Capacity, backlog, and shipment KPIs require unit-aware operating evidence.",),
        statement_overrides={},
    ),
    "consumer": IndustryProfile(
        profile_id="consumer",
        label="Consumer staples/discretionary issuer",
        markets=(Market.EU,),
        required_financial_metrics=("operating_revenue", "gross_profit", "net_profit", "total_assets", "total_equity", "operating_cash_flow_net"),
        optional_financial_metrics=("inventories", "cash_and_cash_equivalents"),
        operating_metric_profiles=("general", "retail"),
        validation_notes=("Organic growth and volume/mix KPIs should remain operating metrics with issuer definitions.",),
        statement_overrides={},
    ),
    "industrial": IndustryProfile(
        profile_id="industrial",
        label="European industrial issuer",
        markets=(Market.EU,),
        required_financial_metrics=("operating_revenue", "gross_profit", "net_profit", "total_assets", "total_liabilities", "operating_cash_flow_net"),
        optional_financial_metrics=("capital_expenditure", "property_plant_equipment"),
        operating_metric_profiles=("general", "manufacturing"),
        validation_notes=("Order intake and backlog are operating KPIs and need source-table definitions.",),
        statement_overrides={},
    ),
    "telecom": IndustryProfile(
        profile_id="telecom",
        label="Telecommunications issuer",
        markets=(Market.HK, Market.EU,),
        required_financial_metrics=("operating_revenue", "net_profit", "total_assets", "total_liabilities", "total_equity", "operating_cash_flow_net"),
        optional_financial_metrics=("capital_expenditure", "lease_liabilities", "right_of_use_assets"),
        operating_metric_profiles=("general",),
        validation_notes=("Subscriber, ARPU, and churn KPIs are operating metrics and should not be inferred from financial tables.",),
        statement_overrides={},
    ),
}


def get_industry_profile(profile_id: str | None) -> IndustryProfile:
    return INDUSTRY_PROFILES.get((profile_id or "general").strip().lower(), INDUSTRY_PROFILES["general"])


def list_industry_profiles() -> list[dict[str, object]]:
    return [
        {
            "profile_id": profile.profile_id,
            "label": profile.label,
            "markets": [market.value for market in profile.markets],
            "required_financial_metrics": list(profile.required_financial_metrics),
            "optional_financial_metrics": list(profile.optional_financial_metrics),
            "operating_metric_profiles": list(profile.operating_metric_profiles),
            "validation_notes": list(profile.validation_notes),
            "statement_overrides": dict(profile.statement_overrides),
        }
        for profile in INDUSTRY_PROFILES.values()
    ]
