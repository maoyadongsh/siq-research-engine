from __future__ import annotations

from ...models import AccountingStandard, Market, RuleProfile
from ..base import MarketModule, MarketStorageProfile
from .rules import EU_LABEL_RULES


PROFILE = RuleProfile(
    market=Market.EU,
    profile_id="eu_ifrs_pdf_tables_v1",
    rule_version="eu_ifrs_rules_v1",
    accounting_standards=[AccountingStandard.IFRS],
    report_forms=["annual", "ESEF", "AFR", "20-F", "URD"],
    preferred_artifacts=["document_full", "content_list", "table_index", "markdown", "ixbrl", "esef_zip"],
    notes=[
        "Prefer ESEF/iXBRL facts when available; PDF table evidence is the P0 fallback.",
        "EU markets share eu_ifrs storage; country remains a filing/company attribute.",
    ],
)

STORAGE = MarketStorageProfile(
    market=Market.EU,
    postgres_database="siq",
    postgres_schema="eu_ifrs",
    wiki_namespace="data/wiki/eu_reports",
    raw_download_root="data/market-report-finder/downloads/EU",
    parsed_artifact_root="data/wiki/eu_reports",
    agent_policy="market_specific_agents_only",
    notes=(
        "EU report facts must stay in the project-managed siq/eu_ifrs namespace.",
        "Do not split UK/FR/DE/NL/CH into separate schemas.",
    ),
)

MARKET_MODULE = MarketModule(
    market=Market.EU,
    code="eu",
    display_name="欧股",
    rule_profile=PROFILE,
    storage_profile=STORAGE,
    rule_count=len(EU_LABEL_RULES),
    parser_boundary="markets.eu",
    notes=("EU IFRS PDF/ESEF rules are isolated under markets/eu.",),
)
