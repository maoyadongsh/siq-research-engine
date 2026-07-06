from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ...models import AccountingStandard, Market, ParsedArtifact, StatementType
from ..xbrl_table_hybrid import HybridMarketSpec, extract_hybrid_artifact
from .rules import find_jp_concept_rule, find_jp_label_rule


JP_SPEC = HybridMarketSpec(
    market=Market.JP,
    profile_id="jp_edinet_xbrl_tables_v1",
    default_currency="JPY",
    default_accounting_standard=AccountingStandard.UNKNOWN,
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
    accounting_standard = _resolve_jp_accounting_standard(artifact)
    if accounting_standard != artifact.accounting_standard:
        artifact = artifact.model_copy(update={"accounting_standard": accounting_standard})
    return extract_hybrid_artifact(artifact, JP_SPEC)


def _resolve_jp_accounting_standard(artifact: ParsedArtifact) -> AccountingStandard:
    if artifact.accounting_standard != AccountingStandard.UNKNOWN:
        return artifact.accounting_standard
    explicit = _explicit_standard_from_metadata(artifact)
    if explicit:
        return explicit
    concept_standard = _standard_from_concepts(_concept_candidates(artifact))
    if concept_standard:
        return concept_standard
    return AccountingStandard.UNKNOWN


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
    if any(token in text for token in ("j-gaap", "j gaap", "japanese gaap", "japan gaap", "日本基準", "日本会計基準")) or "jgaap" in compact:
        return AccountingStandard.JGAAP
    if "ifrs" in compact or "国際財務報告基準" in text:
        return AccountingStandard.IFRS
    return None


def _concept_candidates(artifact: ParsedArtifact) -> Iterator[str]:
    for fact in artifact.facts:
        yield fact.concept
    if isinstance(artifact.document_full, dict):
        yield from _concepts_from_payload(artifact.document_full)


def _concepts_from_payload(payload: Any, taxonomy: str | None = None) -> Iterator[str]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            if key_text in {"concept", "xbrl_tag", "tag", "name"} and isinstance(value, str):
                yield value
            if _looks_like_taxonomy(key_text) and isinstance(value, dict):
                yield from _concepts_from_payload(value, key_text)
                continue
            if taxonomy and _looks_like_concept_name(key_text):
                yield f"{taxonomy}:{key_text}"
            elif _looks_like_prefixed_concept(key_text):
                yield key_text
            yield from _concepts_from_payload(value, taxonomy)
    elif isinstance(payload, list | tuple):
        for item in payload:
            yield from _concepts_from_payload(item, taxonomy)


def _looks_like_taxonomy(value: str) -> bool:
    return value.lower() in {"ifrs-full", "ifrs", "jpcrp_cor", "jppfs_cor", "jpigp_cor"}


def _looks_like_concept_name(value: str) -> bool:
    return bool(value) and value[0].isupper() and value.replace("_", "").replace("-", "").isalnum()


def _looks_like_prefixed_concept(value: str) -> bool:
    prefix = value.split(":", 1)[0].lower()
    return ":" in value and prefix in {"ifrs-full", "ifrs", "jpcrp_cor", "jppfs_cor", "jpigp_cor"}


def _standard_from_concepts(concepts: Iterator[str]) -> AccountingStandard | None:
    seen_jgaap = False
    for concept in concepts:
        normalized = str(concept or "").lower()
        if normalized.startswith(("ifrs", "ifrs-full:")):
            return AccountingStandard.IFRS
        if normalized.startswith(("jpcrp", "jppfs", "jpigp")):
            seen_jgaap = True
    return AccountingStandard.JGAAP if seen_jgaap else None
