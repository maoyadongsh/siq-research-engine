from __future__ import annotations

from ...models import AccountingStandard, Market, RuleProfile
from ..base import MarketModule, MarketStorageProfile
from .rules import KR_CONCEPT_RULES, KR_LABEL_RULES


PROFILE = RuleProfile(
    market=Market.KR,
    profile_id="kr_dart_xbrl_tables_v1",
    rule_version="kr_dart_rules_v1",
    accounting_standards=[AccountingStandard.IFRS, AccountingStandard.KIFRS, AccountingStandard.UNKNOWN],
    report_forms=["annual", "semiannual", "quarterly", "business_report", "quarterly_report", "semiannual_report"],
    preferred_artifacts=["dart_facts", "xbrl_facts", "single_company_accounts", "document_full", "table_index", "markdown"],
    notes=[
        "Prefer DART XBRL/API financial statement facts when available.",
        "Use PDF/HTML table extraction as a fallback and provenance layer.",
    ],
)

STORAGE = MarketStorageProfile(
    market=Market.KR,
    postgres_database="siq",
    postgres_schema="dart_kr",
    wiki_namespace="data/wiki/kr",
    raw_download_root="data/market-report-finder/downloads/KR",
    parsed_artifact_root="data/wiki/kr",
    agent_policy="market_specific_agents_only",
    notes=(
        "KR report facts must stay in the project-managed siq/dart_kr namespace.",
        "KR is modeled closer to SEC because DART provides structured XBRL/API data, with PDF table fallback.",
    ),
)

MARKET_MODULE = MarketModule(
    market=Market.KR,
    code="kr",
    display_name="韩股",
    rule_profile=PROFILE,
    storage_profile=STORAGE,
    rule_count=len(KR_CONCEPT_RULES) + len(KR_LABEL_RULES),
    parser_boundary="markets.kr",
    notes=("DART hybrid XBRL/API/PDF rules are isolated under markets/kr.",),
)
