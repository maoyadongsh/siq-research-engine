from __future__ import annotations

from ...models import AccountingStandard, Market, RuleProfile
from ..base import MarketModule, MarketStorageProfile
from .rules import HK_LABEL_RULES


PROFILE = RuleProfile(
    market=Market.HK,
    profile_id="hkex_pdf_tables_v1",
    rule_version="hkex_rules_v1",
    accounting_standards=[AccountingStandard.HKFRS, AccountingStandard.IFRS, AccountingStandard.CASBE],
    report_forms=["annual", "interim", "quarterly", "q1", "q3"],
    preferred_artifacts=["document_full", "content_list", "table_index", "markdown"],
    notes=[
        "Prefer parsed PDF financial statement tables.",
        "HK issuers may disclose under HKFRS, IFRS, or China Accounting Standards.",
    ],
)

STORAGE = MarketStorageProfile(
    market=Market.HK,
    postgres_database="siq",
    postgres_schema="pdf2md_hk",
    wiki_namespace="data/wiki/hk",
    raw_download_root="data/market-report-finder/downloads/HK",
    parsed_artifact_root="data/wiki/hk",
    agent_policy="market_specific_agents_only",
    notes=(
        "HK report facts must stay in the project-managed siq/pdf2md_hk namespace.",
        "Do not reuse CN/US schemas or collections as an automatic fallback.",
    ),
)

MARKET_MODULE = MarketModule(
    market=Market.HK,
    code="hk",
    display_name="港股",
    rule_profile=PROFILE,
    storage_profile=STORAGE,
    rule_count=len(HK_LABEL_RULES),
    parser_boundary="markets.hk",
    notes=("HKEX PDF table rules are isolated under markets/hk.",),
)
