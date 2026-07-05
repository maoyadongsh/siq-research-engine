from __future__ import annotations

from ...models import AccountingStandard, Market, RuleProfile
from ..base import MarketModule, MarketStorageProfile
from .rules import US_CONCEPT_RULES


PROFILE = RuleProfile(
    market=Market.US,
    profile_id="us_sec_xbrl_v1",
    rule_version="us_sec_rules_v1",
    accounting_standards=[AccountingStandard.US_GAAP, AccountingStandard.IFRS],
    report_forms=["10-K", "10-Q", "20-F", "6-K"],
    preferred_artifacts=["sec_companyfacts", "xbrl_facts", "ixbrl_html", "html"],
    notes=[
        "Prefer XBRL/iXBRL facts over table OCR.",
        "US domestic issuers usually use US GAAP; foreign private issuers may use IFRS.",
    ],
)

STORAGE = MarketStorageProfile(
    market=Market.US,
    postgres_database="siq",
    postgres_schema="sec_us",
    wiki_namespace="data/wiki/us",
    raw_download_root="data/market-report-finder/downloads/US",
    parsed_artifact_root="data/wiki/us",
    agent_policy="market_specific_agents_only",
    notes=(
        "US SEC facts must stay in the project-managed siq/sec_us namespace.",
        "Do not reuse CN/HK schemas or collections as an automatic fallback.",
    ),
)

MARKET_MODULE = MarketModule(
    market=Market.US,
    code="us",
    display_name="美股",
    rule_profile=PROFILE,
    storage_profile=STORAGE,
    rule_count=len(US_CONCEPT_RULES),
    parser_boundary="markets.us",
    notes=("SEC XBRL/iXBRL concept rules are isolated under markets/us.",),
)
