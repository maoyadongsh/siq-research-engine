from __future__ import annotations

from ...models import AccountingStandard, Market, ParsedArtifact, StatementType
from ..xbrl_table_hybrid import HybridMarketSpec, extract_hybrid_artifact
from .rules import find_kr_concept_rule, find_kr_label_rule


KR_SPEC = HybridMarketSpec(
    market=Market.KR,
    profile_id="kr_dart_xbrl_tables_v1",
    default_currency="KRW",
    default_accounting_standard=AccountingStandard.IFRS,
    concept_source_type="dart_xbrl_fact",
    table_source_type="dart_pdf_statement_table",
    concept_taxonomy_fallback="dart_xbrl",
    section_by_statement={
        StatementType.BALANCE_SHEET: "DART financial statements / Balance Sheet",
        StatementType.INCOME_STATEMENT: "DART financial statements / Income Statement",
        StatementType.CASH_FLOW_STATEMENT: "DART financial statements / Cash Flows",
        StatementType.KEY_METRICS: "DART key metrics",
    },
    find_concept_rule=find_kr_concept_rule,
    find_label_rule=find_kr_label_rule,
    companyfacts_keys=("dart_facts", "xbrl_facts", "single_company_accounts", "facts", "companyfacts"),
    warnings_prefix="KR DART hybrid parser",
    skip_ratio_rows=True,
)


def extract_artifact(artifact: ParsedArtifact):
    return extract_hybrid_artifact(artifact, KR_SPEC)
