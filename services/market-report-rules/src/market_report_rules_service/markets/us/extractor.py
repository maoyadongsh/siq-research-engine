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
from ..common import build_result
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
        raw = fact.raw if isinstance(fact.raw, dict) else {}
        raw_value = str(raw.get("value_text") or fact.value)
        extracted.append(
            ExtractedFact(
                canonical_name=rule.canonical_name,
                local_name=fact.concept,
                label=fact.label or fact.concept,
                statement_type=rule.statement_type,
                value=fact.value,
                raw_value=raw_value,
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
                    anchor=raw.get("anchor"),
                    xpath=raw.get("xpath"),
                    html_snippet=raw.get("html_snippet"),
                    rendered_page_number=raw.get("rendered_page_number"),
                    quote_text=raw.get("value_text"),
                    raw=fact.raw,
                ),
                raw=fact.model_dump(mode="json"),
            )
        )

    if not extracted:
        warnings.append("No mapped SEC/XBRL facts were extracted. Check whether the parser supplied facts or companyfacts data.")

    derived = _derive_missing_facts(extracted, artifact)
    if derived:
        extracted.extend(derived)
        warnings.append(f"Derived {len(derived)} US metrics from reported SEC/XBRL facts.")

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


def _derive_missing_facts(facts: list[ExtractedFact], artifact: ParsedArtifact) -> list[ExtractedFact]:
    by_period: dict[str, dict[str, ExtractedFact]] = {}
    for fact in facts:
        if fact.statement_type == StatementType.OPERATING_METRICS:
            continue
        if _fact_dimensions(fact):
            continue
        bucket = by_period.setdefault(fact.period_key, {})
        current = bucket.get(fact.canonical_name)
        if current is None or _fact_rank(fact) > _fact_rank(current):
            bucket[fact.canonical_name] = fact

    derived: list[ExtractedFact] = []
    for pkey, bucket in by_period.items():
        _derive_one(
            derived,
            bucket,
            artifact,
            pkey,
            "total_liabilities",
            StatementType.BALANCE_SHEET,
            (("total_liabilities_and_equity", Decimal("1")), ("total_equity", Decimal("-1"))),
        )
        _derive_one(
            derived,
            bucket,
            artifact,
            pkey,
            "total_equity",
            StatementType.BALANCE_SHEET,
            (("total_liabilities_and_equity", Decimal("1")), ("total_liabilities", Decimal("-1"))),
        )
        _derive_one(
            derived,
            bucket,
            artifact,
            pkey,
            "total_liabilities",
            StatementType.BALANCE_SHEET,
            (("total_assets", Decimal("1")), ("total_equity", Decimal("-1"))),
        )
        _derive_one(
            derived,
            bucket,
            artifact,
            pkey,
            "operating_cash_flow_net",
            StatementType.CASH_FLOW_STATEMENT,
            (
                ("cash_equivalents_net_increase", Decimal("1")),
                ("investing_cash_flow_net", Decimal("-1")),
                ("financing_cash_flow_net", Decimal("-1")),
                ("fx_effect_cash", Decimal("-1")),
            ),
            optional_zero_names={"fx_effect_cash"},
        )
    return derived


def _derive_one(
    out: list[ExtractedFact],
    bucket: dict[str, ExtractedFact],
    artifact: ParsedArtifact,
    period_key_value: str,
    canonical_name: str,
    statement_type: StatementType,
    terms: tuple[tuple[str, Decimal], ...],
    *,
    optional_zero_names: set[str] | None = None,
) -> None:
    if canonical_name in bucket:
        return
    optional_zero_names = optional_zero_names or set()
    components: list[tuple[str, ExtractedFact, Decimal]] = []
    value = Decimal("0")
    for name, sign in terms:
        fact = bucket.get(name)
        if fact is None:
            if name in optional_zero_names:
                continue
            return
        value += fact.value * sign
        components.append((name, fact, sign))
    if not components:
        return
    first = components[0][1]
    confidence = min((fact.confidence for _, fact, _ in components), default=Decimal("0.70")) - Decimal("0.08")
    if confidence < Decimal("0.60"):
        confidence = Decimal("0.60")
    formula = " + ".join(f"{sign}*{name}" for name, _, sign in components)
    derived = ExtractedFact(
        canonical_name=canonical_name,
        local_name=f"derived_{canonical_name}",
        label=f"Derived {canonical_name}",
        statement_type=statement_type,
        value=value,
        raw_value=str(value),
        unit=first.unit,
        currency=first.currency,
        period_key=period_key_value,
        period_start=first.period_start,
        period_end=first.period_end,
        fiscal_year=first.fiscal_year or artifact.fiscal_year,
        fiscal_period=first.fiscal_period or artifact.fiscal_period,
        scale=first.scale,
        market=Market.US,
        accounting_standard=artifact.accounting_standard if artifact.accounting_standard != AccountingStandard.UNKNOWN else AccountingStandard.US_GAAP,
        taxonomy="us_sec_xbrl_derived",
        gaap_status="derived_from_reported_components",
        source_accession=first.source_accession,
        confidence=confidence,
        evidence=EvidenceRef(
            source_type="derived_reported_metric",
            source_id=f"derived:{canonical_name}",
            accession_number=first.source_accession or first.evidence.accession_number,
            url=artifact.source_url,
            section=first.evidence.section,
            anchor=first.evidence.anchor,
            quote_text=f"Derived {canonical_name}: {formula}",
            raw={
                "formula": formula,
                "components": [
                    {
                        "canonical_name": name,
                        "value": str(fact.value),
                        "period_key": fact.period_key,
                        "evidence": fact.evidence.model_dump(mode="json"),
                    }
                    for name, fact, _ in components
                ],
            },
        ),
        raw={"derived": True, "formula": formula, "components": [name for name, _, _ in components]},
    )
    out.append(derived)
    bucket[canonical_name] = derived


def _fact_dimensions(fact: ExtractedFact) -> dict[str, Any]:
    raw = fact.raw if isinstance(fact.raw, dict) else {}
    dimensions = raw.get("dimensions")
    if isinstance(dimensions, dict):
        return dimensions
    evidence_raw = fact.evidence.raw if fact.evidence and isinstance(fact.evidence.raw, dict) else {}
    nested = evidence_raw.get("dimensions")
    return nested if isinstance(nested, dict) else {}


def _fact_rank(fact: ExtractedFact) -> tuple[int, Decimal]:
    source_type = str(fact.evidence.source_type if fact.evidence else "").lower()
    source_rank = 3 if "xbrl" in source_type else 2 if source_type == "derived_reported_metric" else 1
    return (source_rank, fact.confidence)
