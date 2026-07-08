from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ...models import AccountingStandard, Market, ParsedArtifact, StatementType
from ..xbrl_table_hybrid import HybridMarketSpec, extract_hybrid_artifact
from .rules import find_kr_concept_rule, find_kr_label_rule


KR_SPEC = HybridMarketSpec(
    market=Market.KR,
    profile_id="kr_dart_xbrl_tables_v1",
    default_currency="KRW",
    default_accounting_standard=AccountingStandard.KIFRS,
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
    restrict_unclassified_statement_tables=True,
)


def extract_artifact(artifact: ParsedArtifact):
    accounting_standard = _resolve_kr_accounting_standard(artifact)
    if accounting_standard != artifact.accounting_standard:
        artifact = artifact.model_copy(update={"accounting_standard": accounting_standard})
    return extract_hybrid_artifact(artifact, KR_SPEC)


def _resolve_kr_accounting_standard(artifact: ParsedArtifact) -> AccountingStandard:
    if artifact.accounting_standard != AccountingStandard.UNKNOWN:
        return artifact.accounting_standard
    explicit = _explicit_standard_from_metadata(artifact)
    if explicit:
        return explicit
    return AccountingStandard.KIFRS


def _explicit_standard_from_metadata(artifact: ParsedArtifact) -> AccountingStandard | None:
    for value in _metadata_standard_values(artifact.metadata):
        standard = _standard_from_text(value)
        if standard:
            return standard
    for key in ("manifest", "source_manifest", "market_metadata", "metadata"):
        payload = artifact.document_full.get(key) if isinstance(artifact.document_full, dict) else None
        for value in _metadata_standard_values(payload):
            standard = _standard_from_text(value)
            if standard:
                return standard
    return None


_STANDARD_METADATA_KEYS = {
    "accounting_standard",
    "accountingstandard",
    "accounting_basis",
    "accountingbasis",
    "basis_of_accounting",
    "basisofaccounting",
    "gaap",
    "standard",
}


def _metadata_standard_values(payload: Any) -> Iterator[str]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = "".join(ch for ch in str(key).lower() if ch.isalnum() or ch == "_")
            if normalized_key in _STANDARD_METADATA_KEYS and value is not None:
                yield str(value)
            yield from _metadata_standard_values(value)
    elif isinstance(payload, list | tuple):
        for item in payload:
            yield from _metadata_standard_values(item)


def _standard_from_text(value: str) -> AccountingStandard | None:
    text = str(value or "").strip().lower()
    compact = "".join(ch for ch in text if ch.isalnum())
    if not compact or compact in {"unknown", "unk", "notavailable", "na", "n/a"}:
        return AccountingStandard.UNKNOWN
    if "kifrs" in compact or "koreanifrs" in compact or "k-ifrs" in text or "한국채택국제회계기준" in text:
        return AccountingStandard.KIFRS
    if "ifrs" in compact or "국제재무보고기준" in text:
        return AccountingStandard.KIFRS
    return None
