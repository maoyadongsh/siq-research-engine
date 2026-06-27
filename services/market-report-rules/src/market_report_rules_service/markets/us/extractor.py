from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...models import (
    AccountingStandard,
    EvidenceRef,
    ExtractedFact,
    ExtractionResult,
    Market,
    ParsedArtifact,
    ParsedFact,
    StatementType,
)
from ...normalization import parse_date, parse_decimal, period_key
from ...registry import get_profile
from ..common import build_result, extract_operating_metrics_from_tables
from .rules import find_us_rule


def extract_artifact(artifact: ParsedArtifact) -> ExtractionResult:
    profile = get_profile(Market.US)
    facts = list(artifact.facts) or _facts_from_sec_companyfacts(artifact.document_full)
    selected = _select_best_facts(facts, artifact)

    extracted: list[ExtractedFact] = []
    operating: list[ExtractedFact] = []
    warnings: list[str] = []
    for fact in selected:
        rule = find_us_rule(fact.concept)
        if not rule:
            continue
        extracted.append(
            ExtractedFact(
                canonical_name=rule.canonical_name,
                local_name=fact.concept,
                label=fact.label or fact.concept,
                statement_type=rule.statement_type,
                value=fact.value,
                raw_value=str(fact.value),
                unit=fact.unit or artifact.unit,
                currency=_currency(fact.unit, artifact.currency),
                period_key=_fact_period_key(fact, artifact),
                period_start=fact.period_start,
                period_end=fact.period_end or artifact.period_end,
                duration_days=_duration_days(fact),
                frame=fact.frame,
                qtd_ytd_type=_qtd_ytd_type(fact, artifact),
                fiscal_year=fact.fiscal_year or artifact.fiscal_year,
                fiscal_period=fact.fiscal_period or artifact.fiscal_period,
                scale=Decimal("1"),
                market=Market.US,
                accounting_standard=_accounting_standard(artifact, fact.concept),
                taxonomy=_taxonomy_for_concept(fact.concept),
                is_extension=_is_extension_concept(fact.concept),
                gaap_status="reported_gaap",
                source_accession=fact.accession_number,
                confidence=Decimal("0.95"),
                evidence=EvidenceRef(
                    source_type=_source_type_for_fact(fact, artifact),
                    source_id=fact.concept,
                    xbrl_tag=fact.concept,
                    accession_number=fact.accession_number,
                    url=artifact.source_url,
                    section=_sec_section_for_fact(fact),
                    anchor=fact.raw.get("anchor") if isinstance(fact.raw, dict) else None,
                    xpath=fact.raw.get("xpath") if isinstance(fact.raw, dict) else None,
                    html_snippet=fact.raw.get("html_snippet") if isinstance(fact.raw, dict) else None,
                    rendered_page_number=fact.raw.get("rendered_page_number") if isinstance(fact.raw, dict) else None,
                    raw=fact.raw,
                ),
                raw=fact.model_dump(mode="json"),
            )
        )

    if not extracted:
        warnings.append("No mapped SEC/XBRL facts were extracted. Check whether the parser supplied facts or companyfacts data.")

    operating.extend(extract_operating_metrics_from_tables(artifact, artifact.tables, confidence=Decimal("0.78")))

    return build_result(artifact, profile.profile_id, profile.rule_version, extracted, operating, warnings)


def _select_best_facts(facts: list[ParsedFact], artifact: ParsedArtifact) -> list[ParsedFact]:
    best: dict[tuple[str, str, str], tuple[tuple[int, ...], ParsedFact]] = {}
    for fact in facts:
        rule = find_us_rule(fact.concept)
        if not rule:
            continue
        pkey = _fact_period_key(fact, artifact)
        duration_type = _qtd_ytd_type(fact, artifact) or "instant"
        score = (
            0 if artifact.period_end and fact.period_end == artifact.period_end else 1,
            0 if artifact.fiscal_year and fact.fiscal_year == artifact.fiscal_year else 1,
            0 if artifact.report_form and (fact.form or "").upper() == artifact.report_form.upper() else 1,
            _duration_rank(fact, artifact, rule.statement_type),
            _dimension_rank(fact),
            rule.priority,
            -(fact.filed_at.toordinal() if fact.filed_at else 0),
        )
        key = (rule.canonical_name, pkey, duration_type)
        if key not in best or score < best[key][0]:
            best[key] = (score, fact)
    return [item[1] for item in sorted(best.values(), key=lambda item: item[0])]


def _fact_period_key(fact: ParsedFact, artifact: ParsedArtifact) -> str:
    return period_key(fact.period_end or artifact.period_end, fact.fiscal_year or artifact.fiscal_year)


def _duration_days(fact: ParsedFact) -> int | None:
    if fact.duration_days is not None:
        return fact.duration_days
    if fact.period_start and fact.period_end:
        return (fact.period_end - fact.period_start).days + 1
    return None


def _qtd_ytd_type(fact: ParsedFact, artifact: ParsedArtifact) -> str | None:
    if isinstance(fact.raw, dict) and fact.raw.get("qtd_ytd_type"):
        return str(fact.raw["qtd_ytd_type"])
    duration = _duration_days(fact)
    if duration is None:
        return "instant"
    form = (fact.form or artifact.report_form or "").upper()
    fp = (fact.fiscal_period or artifact.fiscal_period or "").upper()
    if form in {"10-K", "20-F"} or fp == "FY" or duration >= 300:
        return "fy"
    if "H1" in fp or 150 <= duration <= 210:
        return "h1"
    if form == "10-Q":
        if duration <= 110:
            return "qtd"
        if duration <= 290:
            return "ytd"
    if duration <= 110:
        return "qtd"
    if duration <= 290:
        return "ytd"
    return "duration"


def _duration_rank(fact: ParsedFact, artifact: ParsedArtifact, statement_type: StatementType) -> int:
    kind = _qtd_ytd_type(fact, artifact)
    form = (fact.form or artifact.report_form or "").upper()
    if statement_type == StatementType.BALANCE_SHEET:
        return 0 if kind == "instant" else 3
    if form == "10-Q":
        order = {"ytd": 0, "qtd": 1, "h1": 2, "duration": 3, "fy": 4, "instant": 5}
        return order.get(kind or "", 9)
    order = {"fy": 0, "h1": 1, "ytd": 2, "qtd": 3, "duration": 4, "instant": 5}
    return order.get(kind or "", 9)


def _dimension_rank(fact: ParsedFact) -> int:
    if isinstance(fact.raw, dict):
        dimensions = fact.raw.get("dimensions")
        if isinstance(dimensions, dict) and dimensions:
            return 1
    return 0


def _currency(unit: str | None, fallback: str | None) -> str | None:
    if unit:
        normalized = unit.upper()
        if normalized in {"USD", "USD/SHARES", "USD/SHARE"}:
            return "USD"
        if normalized in {"EUR", "HKD", "CNY", "JPY", "KRW"}:
            return normalized
    return fallback or "USD"


def _taxonomy_for_concept(concept: str) -> str | None:
    return concept.split(":", 1)[0] if ":" in concept else None


def _is_extension_concept(concept: str) -> bool:
    taxonomy = (_taxonomy_for_concept(concept) or "").lower()
    return taxonomy not in {"us-gaap", "ifrs-full", "dei", "srt", "country"}


def _accounting_standard(artifact: ParsedArtifact, concept: str) -> AccountingStandard:
    if artifact.accounting_standard != AccountingStandard.UNKNOWN:
        return artifact.accounting_standard
    if concept.lower().startswith("ifrs"):
        return AccountingStandard.IFRS
    return AccountingStandard.US_GAAP


def _facts_from_sec_companyfacts(document_full: dict[str, Any]) -> list[ParsedFact]:
    payload = (
        document_full.get("sec_companyfacts")
        or document_full.get("companyfacts")
        or document_full.get("facts")
        or {}
    )
    if not isinstance(payload, dict):
        return []

    facts: list[ParsedFact] = []
    for taxonomy, concepts in payload.items():
        if taxonomy in {"cik", "entityName"} or not isinstance(concepts, dict):
            continue
        for concept, body in concepts.items():
            if not isinstance(body, dict):
                continue
            units = body.get("units") or {}
            for unit, rows in units.items():
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict) or "val" not in row:
                        continue
                    value = parse_decimal(row.get("val"))
                    if value is None:
                        continue
                    facts.append(
                        ParsedFact(
                            concept=f"{taxonomy}:{concept}",
                            value=value,
                            unit=unit,
                            fiscal_year=row.get("fy"),
                            fiscal_period=row.get("fp"),
                            period_start=parse_date(row.get("start")),
                            period_end=parse_date(row.get("end")),
                            duration_days=row.get("duration_days"),
                            filed_at=parse_date(row.get("filed")),
                            form=row.get("form"),
                            frame=row.get("frame"),
                            context_id=row.get("context_id") or row.get("ctxref"),
                            accession_number=row.get("accn"),
                            decimals=row.get("decimals"),
                            label=body.get("label"),
                            raw={**row, "source_type": "sec_companyfacts_fact"},
                        )
                    )
    return facts


def _sec_section_for_fact(fact: ParsedFact) -> str | None:
    if fact.form and fact.form.upper() in {"10-K", "20-F"}:
        rule = find_us_rule(fact.concept)
        if rule:
            if rule.statement_type == StatementType.BALANCE_SHEET:
                return "Item 8 - Financial Statements / Balance Sheet"
            if rule.statement_type == StatementType.INCOME_STATEMENT:
                return "Item 8 - Financial Statements / Income Statement"
            if rule.statement_type == StatementType.CASH_FLOW_STATEMENT:
                return "Item 8 - Financial Statements / Cash Flows"
        return "Item 8 - Financial Statements"
    if fact.form and fact.form.upper() == "10-Q":
        return "Part I, Item 1 - Financial Statements"
    if fact.form and fact.form.upper() == "6-K":
        return "Form 6-K exhibit / interim report"
    return None


def _source_type_for_fact(fact: ParsedFact, artifact: ParsedArtifact) -> str:
    if isinstance(fact.raw, dict) and fact.raw.get("source_type"):
        return str(fact.raw["source_type"])
    if artifact.facts:
        return "sec_xbrl_fact"
    return "sec_companyfacts_fact"
