from __future__ import annotations

from ...models import AccountingStandard, Market, RuleProfile
from ..base import MarketFeaturePage, MarketModule, MarketStorageProfile
from .adapter import CN_LEGACY_RULE_VERSION


PROFILE = RuleProfile(
    market=Market.CN,
    profile_id="cn_pdf2md_legacy_v14",
    rule_version=CN_LEGACY_RULE_VERSION,
    accounting_standards=[AccountingStandard.CASBE],
    report_forms=["annual", "semiannual", "q1", "q3"],
    preferred_artifacts=["document_full", "content_list", "financial_data", "financial_checks"],
    notes=[
        "A-share rules currently live in apps/pdf-parser/financial_extractor.py.",
        "This profile registers the CN market boundary before moving the implementation.",
    ],
)

STORAGE = MarketStorageProfile(
    market=Market.CN,
    postgres_database="siq",
    postgres_schema="pdf2md",
    wiki_namespace="data/wiki/cn_reports",
    raw_download_root="data/market-report-finder/downloads",
    parsed_artifact_root="data/pdf-parser",
    agent_policy="a_share_legacy_agents",
    notes=(
        "CN remains on the existing A-share PostgreSQL/Wiki path until migrated.",
        "Do not share CN parser internals with HK/US modules.",
    ),
)

FEATURE_PAGES = (
    MarketFeaturePage(
        page_id="cn-report-download",
        title="A股财报查询下载",
        owner="services/market-report-finder",
        route_hint="/markets/cn/downloads",
        service_path="/home/maoyd/siq-research-engine/services/market-report-finder",
        status="unified_service",
        notes=("Current UI is served by the unified market-report-finder service.",),
    ),
    MarketFeaturePage(
        page_id="cn-pdf-parsing",
        title="A股 PDF 解析与财务抽取",
        owner="apps/pdf-parser",
        route_hint="/markets/cn/pdf-parsing",
        service_path="/home/maoyd/siq-research-engine/apps/pdf-parser",
        status="legacy_service",
        notes=("Financial rules live in financial_extractor.py as financial_rules_v14.",),
    ),
)

MARKET_MODULE = MarketModule(
    market=Market.CN,
    code="cn",
    display_name="A股",
    rule_profile=PROFILE,
    storage_profile=STORAGE,
    rule_count=0,
    parser_boundary="markets.cn.adapter",
    feature_pages=FEATURE_PAGES,
    notes=(
        "A-share extraction is registered here as a market boundary, not imported into this FastAPI process yet.",
        "Move CN implementation behind this module when the legacy pages are migrated.",
    ),
)
