from __future__ import annotations

from ...models import AccountingStandard, Market, ParsedArtifact, StatementType
from ..xbrl_table_hybrid import HybridMarketSpec, extract_hybrid_artifact
from .rules import find_jp_concept_rule, find_jp_label_rule


JP_SPEC = HybridMarketSpec(
    market=Market.JP,
    profile_id="jp_edinet_xbrl_tables_v1",
    default_currency="JPY",
    default_accounting_standard=AccountingStandard.IFRS,
    concept_source_type="edinet_xbrl_fact",
    table_source_type="edinet_pdf_statement_table",
    concept_taxonomy_fallback="edinet_xbrl",
    section_by_statement={
        StatementType.BALANCE_SHEET: "EDINET financial statements / Balance Sheet",
        StatementType.INCOME_STATEMENT: "EDINET financial statements / Income Statement",
        StatementType.CASH_FLOW_STATEMENT: "EDINET financial statements / Cash Flows",
        StatementType.KEY_METRICS: "EDINET key metrics",
    },
    find_concept_rule=find_jp_concept_rule,
    find_label_rule=find_jp_label_rule,
    companyfacts_keys=("edinet_facts", "xbrl_facts", "facts", "companyfacts"),
    warnings_prefix="JP EDINET hybrid parser",
    allow_mixed_statement_summary_tables=True,
    inherit_adjacent_header_periods=True,
    skip_ratio_rows=True,
)


def extract_artifact(artifact: ParsedArtifact):
    return extract_hybrid_artifact(artifact, JP_SPEC)
