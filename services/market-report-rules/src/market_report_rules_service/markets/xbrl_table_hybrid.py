from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from ..models import (
    AccountingStandard,
    EvidenceRef,
    ExtractedFact,
    ExtractionResult,
    Market,
    ParsedArtifact,
    ParsedFact,
    ParsedTable,
    StatementType,
)
from ..normalization import infer_currency, infer_scale, parse_date, parse_decimal, period_key
from ..registry import get_profile
from ..statement_detection import detect_table_statement_type
from .base import MetricRule
from .common import (
    build_result,
    extract_operating_metrics_from_tables,
    table_period_key,
    tables_from_document_full,
)


@dataclass(frozen=True)
class HybridMarketSpec:
    market: Market
    profile_id: str
    default_currency: str
    default_accounting_standard: AccountingStandard
    concept_source_type: str
    table_source_type: str
    concept_taxonomy_fallback: str
    section_by_statement: dict[StatementType, str]
    find_concept_rule: Callable[[str], MetricRule | None]
    find_label_rule: Callable[[str], MetricRule | None]
    companyfacts_keys: tuple[str, ...]
    warnings_prefix: str


def extract_hybrid_artifact(artifact: ParsedArtifact, spec: HybridMarketSpec) -> ExtractionResult:
    profile = get_profile(spec.market)
    xbrl_facts = list(artifact.facts) or _facts_from_document_full(artifact, spec.companyfacts_keys)
    selected = _select_best_xbrl_facts(xbrl_facts, artifact, spec.find_concept_rule)

    extracted: list[ExtractedFact] = []
    operating: list[ExtractedFact] = []
    warnings: list[str] = []
    seen: set[tuple[str, str, str | None]] = set()

    for fact in selected:
        rule = spec.find_concept_rule(fact.concept)
        if not rule:
            continue
        fact_period_key = _fact_period_key(fact, artifact)
        key = (rule.canonical_name, fact_period_key, _duration_type(fact, artifact))
        if key in seen:
            continue
        seen.add(key)
        extracted.append(
            ExtractedFact(
                canonical_name=rule.canonical_name,
                local_name=fact.concept,
                label=fact.label or fact.concept,
                statement_type=rule.statement_type,
                value=fact.value,
                raw_value=str(fact.value),
                unit=fact.unit or artifact.unit,
                currency=_currency(fact.unit, artifact.currency, spec.default_currency),
                period_key=fact_period_key,
                period_start=fact.period_start,
                period_end=fact.period_end or artifact.period_end,
                duration_days=_duration_days(fact),
                frame=fact.frame,
                qtd_ytd_type=_duration_type(fact, artifact),
                fiscal_year=fact.fiscal_year or artifact.fiscal_year,
                fiscal_period=fact.fiscal_period or artifact.fiscal_period,
                scale=Decimal("1"),
                market=spec.market,
                accounting_standard=_accounting_standard(artifact, fact.concept, spec.default_accounting_standard),
                taxonomy=_taxonomy_for_concept(fact.concept) or spec.concept_taxonomy_fallback,
                is_extension=_is_extension_concept(fact.concept),
                gaap_status="reported_gaap",
                source_accession=fact.accession_number,
                confidence=Decimal("0.93"),
                evidence=EvidenceRef(
                    source_type=spec.concept_source_type,
                    source_id=fact.concept,
                    xbrl_tag=fact.concept,
                    accession_number=fact.accession_number,
                    url=artifact.source_url,
                    section=spec.section_by_statement.get(rule.statement_type),
                    anchor=fact.raw.get("anchor") if isinstance(fact.raw, dict) else None,
                    xpath=fact.raw.get("xpath") if isinstance(fact.raw, dict) else None,
                    html_snippet=fact.raw.get("html_snippet") if isinstance(fact.raw, dict) else None,
                    rendered_page_number=fact.raw.get("rendered_page_number") if isinstance(fact.raw, dict) else None,
                    raw=fact.raw,
                ),
                raw=fact.model_dump(mode="json"),
            )
        )

    tables = list(artifact.tables) or tables_from_document_full(artifact.document_full)
    table_facts = _extract_table_facts(artifact, tables, spec, seen)
    extracted.extend(table_facts)
    operating.extend(extract_operating_metrics_from_tables(artifact, tables, confidence=Decimal("0.74")))

    if not xbrl_facts:
        warnings.append(f"{spec.warnings_prefix}: no XBRL/API facts supplied; used PDF/HTML tables when available.")
    if not extracted:
        warnings.append(f"{spec.warnings_prefix}: no mapped financial facts were extracted. Check XBRL concepts, local-language table labels, or parser table quality.")

    return build_result(artifact, profile.profile_id, profile.rule_version, extracted, operating, warnings)


def _extract_table_facts(
    artifact: ParsedArtifact,
    tables: list[ParsedTable],
    spec: HybridMarketSpec,
    seen: set[tuple[str, str, str | None]],
) -> list[ExtractedFact]:
    extracted: list[ExtractedFact] = []
    table_seen: set[tuple[str, str, int | None, int, int]] = set()
    for table in tables:
        detected_statement_type = detect_table_statement_type(table)
        period_columns = _period_columns_for_table(table, artifact, detected_statement_type)
        table_unit = table.unit or artifact.unit
        table_currency = infer_currency(table.currency, table.unit, table.title, artifact.currency, default=artifact.currency or spec.default_currency)
        scale = infer_scale(table_unit)
        for row_index, row in enumerate(table.rows):
            if len(row) < 2 or row_index in period_columns.header_rows:
                continue
            label = str(row[0] or "").strip()
            rule = spec.find_label_rule(label)
            if not rule:
                continue
            if detected_statement_type and rule.statement_type != detected_statement_type and rule.statement_type != StatementType.KEY_METRICS:
                continue
            for column_index, value in _numeric_cells_for_periods(row, period_columns.column_periods):
                row_period_key = period_columns.column_periods.get(column_index) or table_period_key(artifact, table)
                xbrl_key = (rule.canonical_name, row_period_key, None)
                table_key = (rule.canonical_name, row_period_key, table.table_index, row_index, column_index)
                if xbrl_key in seen or table_key in table_seen:
                    continue
                table_seen.add(table_key)
                extracted.append(
                    ExtractedFact(
                        canonical_name=rule.canonical_name,
                        local_name=label,
                        label=label,
                        statement_type=rule.statement_type,
                        value=value,
                        raw_value=str(row[column_index]) if column_index < len(row) else None,
                        unit=table_unit,
                        currency=table_currency,
                        period_key=row_period_key,
                        period_end=parse_date(row_period_key),
                        fiscal_year=_year_from_period(row_period_key) or artifact.fiscal_year,
                        fiscal_period=artifact.fiscal_period,
                        scale=scale,
                        market=spec.market,
                        accounting_standard=_accounting_standard(artifact, "", spec.default_accounting_standard),
                        taxonomy=f"{spec.market.value.lower()}_pdf_table",
                        gaap_status="reported_gaap",
                        confidence=Decimal("0.80") if detected_statement_type else Decimal("0.72"),
                        evidence=EvidenceRef(
                            source_type=spec.table_source_type if detected_statement_type else "parsed_financial_table",
                            source_id=table.table_id,
                            page_number=table.page_number,
                            table_index=table.table_index,
                            row_index=row_index,
                            column_index=column_index,
                            url=artifact.source_url,
                            quote_text=" | ".join(str(cell) for cell in row),
                            raw={
                                "detected_statement_type": detected_statement_type.value if detected_statement_type else None,
                                "period_columns": period_columns.column_periods,
                                "table": table.raw,
                                "row": row,
                            },
                        ),
                        raw={
                            "table_id": table.table_id,
                            "row": row,
                            "detected_statement_type": detected_statement_type.value if detected_statement_type else None,
                        },
                    )
                )
    return extracted


class _PeriodColumns:
    def __init__(self, column_periods: dict[int, str], header_rows: set[int]):
        self.column_periods = column_periods
        self.header_rows = header_rows


def _period_columns_for_table(
    table: ParsedTable,
    artifact: ParsedArtifact,
    statement_type: StatementType | None,
) -> _PeriodColumns:
    from_raw = _period_columns_from_raw(table.raw)
    if from_raw:
        return _PeriodColumns(from_raw, set())

    best_row_index: int | None = None
    best_periods: dict[int, str] = {}
    for row_index, row in enumerate(table.rows[:5]):
        periods: dict[int, str] = {}
        for column_index, cell in enumerate(row):
            if column_index == 0 or _is_note_column(cell):
                continue
            parsed = _period_from_header_cell(cell, artifact, statement_type)
            if parsed:
                periods[column_index] = parsed
        if len(periods) > len(best_periods):
            best_row_index = row_index
            best_periods = periods

    if best_periods:
        return _PeriodColumns(best_periods, {best_row_index} if best_row_index is not None else set())

    fallback = table_period_key(artifact, table)
    max_columns = max((len(row) for row in table.rows), default=1)
    return _PeriodColumns({index: fallback for index in range(1, max_columns) if not _is_note_column(_cell_at(table.rows[0] if table.rows else [], index))}, set())


def _period_columns_from_raw(raw: dict[str, Any]) -> dict[int, str]:
    columns = raw.get("columns") if isinstance(raw, dict) else None
    if not isinstance(columns, list):
        return {}
    periods: dict[int, str] = {}
    for index, column in enumerate(columns):
        if not isinstance(column, dict):
            continue
        period = column.get("period_key") or column.get("period_end") or column.get("label")
        parsed_date = parse_date(period)
        if parsed_date:
            periods[index] = parsed_date.isoformat()
        elif period:
            periods[index] = str(period)
    return {index: period for index, period in periods.items() if index > 0}


def _period_from_header_cell(cell: Any, artifact: ParsedArtifact, statement_type: StatementType | None) -> str | None:
    text = str(cell or "").strip()
    parsed = parse_date(text)
    if parsed:
        return parsed.isoformat()
    year_match = re.search(r"(20\d{2}|19\d{2})", text)
    if not year_match:
        return None
    year = int(year_match.group(1))
    if statement_type == StatementType.BALANCE_SHEET and artifact.period_end:
        return artifact.period_end.replace(year=year).isoformat()
    if artifact.period_end and artifact.period_end.year == year:
        return artifact.period_end.isoformat()
    if artifact.period_end:
        return artifact.period_end.replace(year=year).isoformat()
    return str(year)


def _numeric_cells_for_periods(row: list[Any], column_periods: dict[int, str]) -> list[tuple[int, Decimal]]:
    values: list[tuple[int, Decimal]] = []
    for column_index in sorted(column_periods):
        if column_index >= len(row):
            continue
        value = parse_decimal(row[column_index])
        if value is not None:
            values.append((column_index, value))
    return values


def _is_note_column(cell: Any) -> bool:
    text = str(cell or "").strip().lower()
    return text in {"note", "notes", "注記", "주석", "註", "附注", "附註"}


def _cell_at(row: list[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _year_from_period(period: str) -> int | None:
    match = re.match(r"(\d{4})", period or "")
    return int(match.group(1)) if match else None


def _select_best_xbrl_facts(
    facts: list[ParsedFact],
    artifact: ParsedArtifact,
    find_rule: Callable[[str], MetricRule | None],
) -> list[ParsedFact]:
    best: dict[tuple[str, str, str], tuple[tuple[int, int, int, int, int], ParsedFact]] = {}
    for fact in facts:
        rule = find_rule(fact.concept)
        if not rule:
            continue
        pkey = _fact_period_key(fact, artifact)
        duration_type = _duration_type(fact, artifact) or "instant"
        score = (
            0 if artifact.period_end and fact.period_end == artifact.period_end else 1,
            0 if artifact.fiscal_year and fact.fiscal_year == artifact.fiscal_year else 1,
            _duration_rank(fact, artifact, rule.statement_type),
            _dimension_rank(fact),
            rule.priority,
        )
        key = (rule.canonical_name, pkey, duration_type)
        if key not in best or score < best[key][0]:
            best[key] = (score, fact)
    return [item[1] for item in sorted(best.values(), key=lambda item: item[0])]


def _facts_from_document_full(artifact: ParsedArtifact, companyfacts_keys: tuple[str, ...]) -> list[ParsedFact]:
    payload = _first_mapping(artifact.document_full, companyfacts_keys)
    if not payload:
        return []
    if _looks_like_concept_map(payload):
        return _facts_from_concept_map(payload, artifact)
    return _facts_from_taxonomy_map(payload, artifact)


def _first_mapping(document_full: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    for key in keys:
        value = document_full.get(key)
        if isinstance(value, dict):
            return value
    nested = document_full.get("xbrl")
    if isinstance(nested, dict):
        for key in keys:
            value = nested.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _looks_like_concept_map(payload: dict[str, Any]) -> bool:
    return any(isinstance(value, dict) and ("units" in value or "value" in value or "val" in value) for value in payload.values())


def _facts_from_taxonomy_map(payload: dict[str, Any], artifact: ParsedArtifact) -> list[ParsedFact]:
    facts: list[ParsedFact] = []
    for taxonomy, concepts in payload.items():
        if taxonomy in {"cik", "entityName"} or not isinstance(concepts, dict):
            continue
        for concept, body in concepts.items():
            facts.extend(_facts_from_body(str(concept), body, artifact, taxonomy=str(taxonomy)))
    return facts


def _facts_from_concept_map(payload: dict[str, Any], artifact: ParsedArtifact) -> list[ParsedFact]:
    facts: list[ParsedFact] = []
    for concept, body in payload.items():
        if isinstance(body, list):
            for row in body:
                facts.extend(_facts_from_body(str(concept), row, artifact))
            continue
        facts.extend(_facts_from_body(str(concept), body, artifact))
    return facts


def _facts_from_body(concept: str, body: Any, artifact: ParsedArtifact, taxonomy: str | None = None) -> list[ParsedFact]:
    if not isinstance(body, dict):
        return []
    units = body.get("units")
    if isinstance(units, dict):
        facts: list[ParsedFact] = []
        for unit, rows in units.items():
            if not isinstance(rows, list):
                continue
            for row in rows:
                facts.extend(_fact_from_row(concept, row, artifact, unit=str(unit), label=body.get("label"), taxonomy=taxonomy))
        return facts
    return _fact_from_row(concept, body, artifact, unit=body.get("unit"), label=body.get("label"), taxonomy=taxonomy)


def _fact_from_row(
    concept: str,
    row: Any,
    artifact: ParsedArtifact,
    *,
    unit: str | None,
    label: Any,
    taxonomy: str | None = None,
) -> list[ParsedFact]:
    if not isinstance(row, dict):
        return []
    value = parse_decimal(row.get("val", row.get("value")))
    if value is None:
        return []
    prefixed_concept = concept if ":" in concept or not taxonomy else f"{taxonomy}:{concept}"
    return [
        ParsedFact(
            concept=prefixed_concept,
            value=value,
            unit=unit or row.get("unit") or row.get("currency") or artifact.currency,
            fiscal_year=row.get("fy") or row.get("fiscal_year") or artifact.fiscal_year,
            fiscal_period=row.get("fp") or row.get("fiscal_period") or artifact.fiscal_period,
            period_start=parse_date(row.get("start") or row.get("period_start")),
            period_end=parse_date(row.get("end") or row.get("period_end")) or artifact.period_end,
            duration_days=row.get("duration_days"),
            filed_at=parse_date(row.get("filed") or row.get("filing_date") or row.get("submit_date")),
            form=row.get("form") or artifact.report_form,
            frame=row.get("frame"),
            context_id=row.get("context_id") or row.get("ctxref") or row.get("contextRef"),
            accession_number=row.get("accn") or row.get("accession_number") or row.get("doc_id") or row.get("rcept_no"),
            decimals=row.get("decimals"),
            label=str(label or row.get("label") or row.get("account_nm") or prefixed_concept),
            raw=row,
        )
    ]


def _fact_period_key(fact: ParsedFact, artifact: ParsedArtifact) -> str:
    return period_key(fact.period_end or artifact.period_end, fact.fiscal_year or artifact.fiscal_year)


def _duration_days(fact: ParsedFact) -> int | None:
    if fact.duration_days is not None:
        return fact.duration_days
    if fact.period_start and fact.period_end:
        return (fact.period_end - fact.period_start).days + 1
    return None


def _duration_type(fact: ParsedFact, artifact: ParsedArtifact) -> str | None:
    if isinstance(fact.raw, dict) and fact.raw.get("qtd_ytd_type"):
        return str(fact.raw["qtd_ytd_type"])
    duration = _duration_days(fact)
    if duration is None:
        return "instant"
    fp = (fact.fiscal_period or artifact.fiscal_period or "").upper()
    if fp == "FY" or duration >= 300:
        return "fy"
    if "H1" in fp or "2Q" in fp or 150 <= duration <= 210:
        return "h1"
    if duration <= 110:
        return "qtd"
    if duration <= 290:
        return "ytd"
    return "duration"


def _duration_rank(fact: ParsedFact, artifact: ParsedArtifact, statement_type: StatementType) -> int:
    kind = _duration_type(fact, artifact)
    if statement_type == StatementType.BALANCE_SHEET:
        return 0 if kind == "instant" else 3
    report_type = (artifact.report_type or artifact.report_form or "").lower()
    if "quarter" in report_type or "quarter" in (fact.form or "").lower():
        order = {"ytd": 0, "qtd": 1, "h1": 2, "fy": 3, "duration": 4, "instant": 5}
        return order.get(kind or "", 9)
    order = {"fy": 0, "h1": 1, "ytd": 2, "qtd": 3, "duration": 4, "instant": 5}
    return order.get(kind or "", 9)


def _dimension_rank(fact: ParsedFact) -> int:
    if isinstance(fact.raw, dict):
        dimensions = fact.raw.get("dimensions") or fact.raw.get("segment")
        if isinstance(dimensions, dict) and dimensions:
            return 1
    return 0


def _currency(unit: str | None, fallback: str | None, default: str) -> str | None:
    if unit:
        normalized = unit.upper()
        for currency in ("USD", "EUR", "HKD", "CNY", "JPY", "KRW"):
            if currency in normalized:
                return currency
    return fallback or default


def _taxonomy_for_concept(concept: str) -> str | None:
    return concept.split(":", 1)[0] if ":" in concept else None


def _is_extension_concept(concept: str) -> bool:
    taxonomy = (_taxonomy_for_concept(concept) or "").lower()
    if not taxonomy:
        return False
    standard_prefixes = ("us-gaap", "ifrs", "jpcrp", "jppfs", "dart", "ifrs-full")
    return not any(taxonomy.startswith(prefix) for prefix in standard_prefixes)


def _accounting_standard(
    artifact: ParsedArtifact,
    concept: str,
    default: AccountingStandard,
) -> AccountingStandard:
    if artifact.accounting_standard != AccountingStandard.UNKNOWN:
        return artifact.accounting_standard
    if concept.lower().startswith("ifrs") or "ifrs" in " ".join(str(value).lower() for value in artifact.metadata.values()):
        return AccountingStandard.IFRS
    return default
