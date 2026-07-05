from __future__ import annotations

from ...models import AccountingStandard, Market, RuleProfile
from ..base import MarketModule, MarketStorageProfile
from .rules import JP_CONCEPT_RULES, JP_LABEL_RULES


PROFILE = RuleProfile(
    market=Market.JP,
    profile_id="jp_edinet_xbrl_tables_v1",
    rule_version="jp_edinet_rules_v1",
    accounting_standards=[AccountingStandard.IFRS, AccountingStandard.JGAAP, AccountingStandard.UNKNOWN],
    report_forms=["annual", "semiannual", "quarterly", "有価証券報告書", "半期報告書", "四半期報告書"],
    preferred_artifacts=["edinet_facts", "xbrl_facts", "ixbrl_html", "document_full", "table_index", "markdown"],
    notes=[
        "Prefer EDINET XBRL/iXBRL facts for listed-company securities reports.",
        "Use PDF/HTML table extraction as a fallback for older filings or non-standard disclosures.",
    ],
)

STORAGE = MarketStorageProfile(
    market=Market.JP,
    postgres_database="siq",
    postgres_schema="edinet_jp",
    wiki_namespace="data/wiki/jp",
    raw_download_root="data/market-report-finder/downloads/JP",
    parsed_artifact_root="data/wiki/jp",
    agent_policy="market_specific_agents_only",
    notes=(
        "JP report facts must stay in the project-managed siq/edinet_jp namespace.",
        "JP is modeled closer to SEC because EDINET provides XBRL, with PDF table fallback for provenance.",
    ),
)

MARKET_MODULE = MarketModule(
    market=Market.JP,
    code="jp",
    display_name="日股",
    rule_profile=PROFILE,
    storage_profile=STORAGE,
    rule_count=len(JP_CONCEPT_RULES) + len(JP_LABEL_RULES),
    parser_boundary="markets.jp",
    notes=("EDINET hybrid XBRL/PDF rules are isolated under markets/jp.",),
)
