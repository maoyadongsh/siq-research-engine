from __future__ import annotations

import re
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
    ParsedTable,
    StatementType,
)
from ...normalization import compact_label, infer_currency, infer_scale, parse_date, parse_decimal, period_key
from ...registry import get_profile
from ...statement_detection import detect_table_statement_type
from ..common import build_result, extract_operating_metrics_from_tables, first_numeric_cell, table_period_key, tables_from_document_full
from .rules import find_eu_label_rule


def extract_artifact(artifact: ParsedArtifact) -> ExtractionResult:
    profile = get_profile(Market.EU)
    tables = list(artifact.tables) or tables_from_document_full(artifact.document_full)

    extracted: list[ExtractedFact] = []
    operating: list[ExtractedFact] = []
    warnings: list[str] = []
    fact_extracted, fact_warnings = _extract_xbrl_facts(artifact)
    extracted.extend(fact_extracted)
    warnings.extend(fact_warnings)

    seen: set[tuple[str, str, int | None, int, int]] = set()
    for table in tables:
        detected_statement_type = detect_table_statement_type(table)
        period_columns = _period_columns_for_table(table, artifact, detected_statement_type)
        table_unit = table.unit or artifact.unit
        table_currency = infer_currency(table.currency, table.unit, table.title, artifact.currency, default=artifact.currency)
        scale = infer_scale(table_unit)
        for row_index, row in enumerate(table.rows):
            if len(row) < 2 or row_index in period_columns.header_rows:
                continue
            label = _row_label(row, period_columns.label_columns)
            rule = find_eu_label_rule(label)
            if not rule:
                continue
            if (
                detected_statement_type
                and rule.statement_type != detected_statement_type
                and rule.statement_type != StatementType.KEY_METRICS
                and not _allow_cross_statement_fact(rule, label, detected_statement_type)
            ):
                continue
            for column_index, value in _numeric_cells_for_periods(row, period_columns.column_periods):
                row_period_key = period_columns.column_periods.get(column_index) or table_period_key(artifact, table)
                key = (rule.canonical_name, row_period_key, table.table_index, row_index, column_index)
                if key in seen:
                    continue
                seen.add(key)
                source_type = _table_source_type(table, detected_statement_type)
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
                        market=Market.EU,
                        accounting_standard=AccountingStandard.IFRS,
                        taxonomy="eu_ifrs_pdf_table",
                        gaap_status="reported_gaap",
                        confidence=Decimal("0.84") if detected_statement_type else Decimal("0.76"),
                        evidence=EvidenceRef(
                            source_type=source_type,
                            source_id=table.table_id,
                            page_number=table.page_number,
                            table_index=table.table_index,
                            row_index=row_index,
                            column_index=column_index,
                            url=artifact.source_url,
                            anchor=_table_anchor(table),
                            xpath=_table_xpath(table),
                            quote_text=" | ".join(str(cell) for cell in row),
                            raw={
                                "country": artifact.metadata.get("country"),
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
                            "document_format": artifact.metadata.get("document_format") or "pdf",
                        },
                    )
                )

        if detected_statement_type is None:
            operating.extend(extract_operating_metrics_from_tables(artifact, [table], confidence=Decimal("0.74")))

    if not extracted:
        warnings.append("No mapped EU IFRS/PDF table rows were extracted. Check table parsing quality or add issuer-specific aliases.")

    derived = _derive_missing_facts(extracted, artifact)
    if derived:
        extracted.extend(derived)
        warnings.append(f"Derived {len(derived)} EU metrics from reported component facts.")

    if artifact.industry_profile in {"bank", "insurance"}:
        warnings.append("EU bank/insurance profile: cash-flow statement coverage may be partial and should be reviewed manually.")

    return build_result(artifact, profile.profile_id, profile.rule_version, extracted, operating, warnings)


def _extract_xbrl_facts(artifact: ParsedArtifact) -> tuple[list[ExtractedFact], list[str]]:
    if not artifact.facts:
        return [], []
    selected = _select_best_xbrl_facts(artifact.facts, artifact)
    extracted: list[ExtractedFact] = []
    warnings: list[str] = []
    extension_count = 0
    for fact, rule in selected:
        taxonomy = _taxonomy_for_concept(fact.concept)
        is_extension = _is_extension_concept(fact.concept)
        if is_extension:
            extension_count += 1
        raw = fact.raw if isinstance(fact.raw, dict) else {}
        source_type = str(raw.get("source_type") or "eu_xbrl_fact")
        extracted.append(
            ExtractedFact(
                canonical_name=rule.canonical_name,
                local_name=fact.concept,
                label=fact.label or fact.concept,
                statement_type=rule.statement_type,
                value=fact.value,
                raw_value=str(raw.get("value_text") or fact.value),
                unit=fact.unit or artifact.unit,
                currency=infer_currency(fact.unit, artifact.currency, default=artifact.currency),
                period_key=period_key(fact.period_end or artifact.period_end, fact.fiscal_year or artifact.fiscal_year),
                period_start=fact.period_start,
                period_end=fact.period_end or artifact.period_end,
                duration_days=fact.duration_days,
                fiscal_year=fact.fiscal_year or artifact.fiscal_year,
                fiscal_period=fact.fiscal_period or artifact.fiscal_period,
                scale=Decimal("1"),
                market=Market.EU,
                accounting_standard=AccountingStandard.IFRS,
                taxonomy=taxonomy,
                is_extension=is_extension,
                gaap_status="extension_mapped_to_ifrs_alias" if is_extension else "reported_gaap",
                source_accession=fact.accession_number or raw.get("source_file"),
                confidence=Decimal("0.72") if is_extension else Decimal("0.94"),
                evidence=EvidenceRef(
                    source_type=source_type,
                    source_id=fact.concept,
                    xbrl_tag=fact.concept,
                    accession_number=fact.accession_number,
                    url=artifact.source_url,
                    anchor=raw.get("html_anchor") or raw.get("anchor"),
                    xpath=raw.get("xpath"),
                    html_snippet=raw.get("html_snippet"),
                    quote_text=str(raw.get("value_text") or fact.value),
                    path=raw.get("source_file"),
                    raw={
                        **raw,
                        "country": artifact.metadata.get("country"),
                        "context_ref": fact.context_id or raw.get("context_ref"),
                        "unit_ref": raw.get("unit_ref"),
                        "fact_id": raw.get("fact_id"),
                    },
                ),
                raw={**raw, "document_format": artifact.metadata.get("document_format") or "ixbrl_xhtml"},
            )
        )
    if extension_count:
        warnings.append(f"Mapped {extension_count} EU extension XBRL facts with reduced confidence; review issuer-specific taxonomy labels.")
    if not extracted:
        warnings.append("EU XBRL facts were present, but no mapped IFRS concepts were extracted.")
    return extracted, warnings


def _select_best_xbrl_facts(facts: list[ParsedFact], artifact: ParsedArtifact) -> list[tuple[ParsedFact, Any]]:
    best: dict[tuple[str, str], tuple[tuple[int, ...], ParsedFact, Any]] = {}
    for fact in facts:
        rule = find_eu_label_rule(fact.concept) or find_eu_label_rule(fact.label or "")
        if not rule:
            continue
        fact_period_key = period_key(fact.period_end or artifact.period_end, fact.fiscal_year or artifact.fiscal_year)
        score = (
            0 if artifact.period_end and fact.period_end == artifact.period_end else 1,
            0 if artifact.fiscal_year and fact.fiscal_year == artifact.fiscal_year else 1,
            _duration_rank_for_xbrl_fact(fact, artifact, rule.statement_type),
            _dimension_rank_for_xbrl_fact(fact),
            1 if _is_extension_concept(fact.concept) else 0,
            rule.priority,
        )
        key = (rule.canonical_name, fact_period_key)
        if key not in best or score < best[key][0]:
            best[key] = (score, fact, rule)
    return [(fact, rule) for _score, fact, rule in sorted(best.values(), key=lambda item: item[0])]


def _duration_rank_for_xbrl_fact(fact: ParsedFact, artifact: ParsedArtifact, statement_type: StatementType) -> int:
    if statement_type == StatementType.BALANCE_SHEET:
        return 0 if fact.duration_days is None else 3
    duration = fact.duration_days
    if duration is None and fact.period_start and fact.period_end:
        duration = (fact.period_end - fact.period_start).days + 1
    if duration is None:
        return 5
    if (artifact.fiscal_period or "").upper() == "FY" or duration >= 300:
        return 0
    if 150 <= duration <= 210:
        return 1
    return 2


def _dimension_rank_for_xbrl_fact(fact: ParsedFact) -> int:
    dimensions = fact.raw.get("dimensions") if isinstance(fact.raw, dict) else None
    return 1 if isinstance(dimensions, dict) and dimensions else 0


def _taxonomy_for_concept(concept: str) -> str | None:
    return concept.split(":", 1)[0] if ":" in concept else None


def _is_extension_concept(concept: str) -> bool:
    taxonomy = (_taxonomy_for_concept(concept) or "").lower()
    return bool(taxonomy) and taxonomy not in {"ifrs-full", "ifrs", "esef_cor", "esef-cor", "dei", "country"}


class _PeriodColumns:
    def __init__(self, column_periods: dict[int, str], header_rows: set[int], label_columns: int = 1):
        self.column_periods = column_periods
        self.header_rows = header_rows
        self.label_columns = label_columns


def _period_columns_for_table(
    table: ParsedTable,
    artifact: ParsedArtifact,
    statement_type: StatementType | None,
) -> _PeriodColumns:
    label_columns = _label_column_count(table.rows)
    from_raw = _period_columns_from_raw(table.raw)
    if from_raw:
        return _PeriodColumns(from_raw, set(), label_columns)

    best_row_index: int | None = None
    best_periods: dict[int, str] = {}
    max_columns = max((len(row) for row in table.rows), default=1)
    for row_index, row in enumerate(table.rows[:5]):
        aligned_row = _aligned_header_row(row, max_columns, label_columns)
        periods: dict[int, str] = {}
        for column_index, cell in enumerate(aligned_row):
            if column_index < label_columns or _is_note_column(cell):
                continue
            parsed = _period_from_header_cell(cell, artifact, statement_type)
            if parsed:
                periods[column_index] = parsed
        if len(periods) > len(best_periods):
            best_row_index = row_index
            best_periods = periods

    if best_periods:
        return _PeriodColumns(best_periods, {best_row_index} if best_row_index is not None else set(), label_columns)

    fallback = table_period_key(artifact, table)
    header = _aligned_header_row(table.rows[0], max_columns, label_columns) if table.rows else []
    periods = {
        column_index: fallback
        for column_index in range(label_columns, max_columns)
        if not _is_note_column(_cell_at(header, column_index))
    }
    return _PeriodColumns(periods, set(), label_columns)


def _table_source_type(table: ParsedTable, statement_type: StatementType | None) -> str:
    raw = table.raw if isinstance(table.raw, dict) else {}
    raw_source = str(raw.get("source_type") or "")
    if raw_source == "html_table" or raw.get("html_anchor") or raw.get("xpath"):
        return "html_table"
    return "pdf_statement_table" if statement_type else "pdf_table"


def _table_anchor(table: ParsedTable) -> str | None:
    raw = table.raw if isinstance(table.raw, dict) else {}
    return raw.get("html_anchor") or raw.get("anchor")


def _table_xpath(table: ParsedTable) -> str | None:
    raw = table.raw if isinstance(table.raw, dict) else {}
    return raw.get("xpath")


def _label_column_count(rows: list[list[Any]]) -> int:
    note_like_second_column = 0
    for row in rows[:50]:
        if len(row) < 4:
            continue
        second = str(row[1] or "").strip()
        if _is_note_column(second) or re.fullmatch(r"\d+[A-Za-z]?", second):
            note_like_second_column += 1
    return 1 if note_like_second_column else 1


def _row_label(row: list[Any], label_columns: int) -> str:
    parts = [str(cell or "").strip() for cell in row[:label_columns]]
    return " ".join(part for part in parts if part) or str(row[0] or "").strip()


def _aligned_header_row(row: list[Any], max_columns: int, label_columns: int) -> list[Any]:
    aligned = list(row)
    if label_columns > 1 and len(aligned) < max_columns:
        missing = max_columns - len(aligned)
        aligned = aligned[:1] + [""] * missing + aligned[1:]
    if len(aligned) < max_columns:
        aligned.extend([""] * (max_columns - len(aligned)))
    return aligned


def _allow_cross_statement_fact(rule: Any, label: str, detected_statement_type: StatementType) -> bool:
    if detected_statement_type != StatementType.INCOME_STATEMENT or rule.statement_type != StatementType.BALANCE_SHEET:
        return False
    normalized = compact_label(label)
    return normalized in {
        "totalassets",
        "assets",
        "totalliabilities",
        "liabilities",
        "totalequity",
        "equity",
        "netassets",
    }


def _period_columns_from_raw(raw: dict[str, Any]) -> dict[int, str]:
    columns = raw.get("columns") if isinstance(raw, dict) else None
    if not isinstance(columns, list):
        return {}
    periods: dict[int, str] = {}
    for index, column in enumerate(columns):
        if not isinstance(column, dict):
            continue
        period = column.get("period_key") or column.get("period_end") or column.get("label")
        parsed_date = _safe_parse_date(period)
        if parsed_date:
            periods[index] = parsed_date.isoformat()
        elif period:
            periods[index] = str(period)
    return {index: period for index, period in periods.items() if index > 0}


def _period_from_header_cell(cell: Any, artifact: ParsedArtifact, statement_type: StatementType | None) -> str | None:
    text = str(cell or "").strip()
    parsed = _safe_parse_date(text)
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


def _safe_parse_date(value: Any):
    try:
        return parse_date(value)
    except (TypeError, ValueError):
        return None


def _numeric_cells_for_periods(row: list[Any], column_periods: dict[int, str]) -> list[tuple[int, Decimal]]:
    values: list[tuple[int, Decimal]] = []
    for column_index in sorted(column_periods):
        if column_index >= len(row):
            continue
        value = parse_decimal(row[column_index])
        if value is not None:
            values.append((column_index, value))
    if values:
        return values
    value, offset = first_numeric_cell(row[1:])
    if value is None or offset is None:
        return []
    return [(offset + 1, value)]


def _is_note_column(cell: Any) -> bool:
    text = compact_label(cell)
    return text in {"note", "notes", "note1", "notenumber"}


def _cell_at(row: list[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _year_from_period(period: str) -> int | None:
    match = re.match(r"(\d{4})", period or "")
    return int(match.group(1)) if match else None


def _derive_missing_facts(facts: list[ExtractedFact], artifact: ParsedArtifact) -> list[ExtractedFact]:
    by_period: dict[str, dict[str, ExtractedFact]] = {}
    for fact in facts:
        bucket = by_period.setdefault(fact.period_key, {})
        current = bucket.get(fact.canonical_name)
        if current is None or fact.confidence > current.confidence:
            bucket[fact.canonical_name] = fact

    derived: list[ExtractedFact] = []
    for period_key, bucket in by_period.items():
        _derive_one(
            derived,
            bucket,
            artifact,
            period_key,
            "net_profit",
            StatementType.INCOME_STATEMENT,
            (("total_profit", Decimal("1")), ("income_tax_expense", Decimal("-1"))),
            expense_names={"income_tax_expense"},
        )
        _derive_one(
            derived,
            bucket,
            artifact,
            period_key,
            "total_equity",
            StatementType.BALANCE_SHEET,
            (("total_assets", Decimal("1")), ("total_liabilities", Decimal("-1"))),
        )
        _derive_one(
            derived,
            bucket,
            artifact,
            period_key,
            "total_liabilities",
            StatementType.BALANCE_SHEET,
            (("total_assets", Decimal("1")), ("total_equity", Decimal("-1"))),
        )
        _derive_one(
            derived,
            bucket,
            artifact,
            period_key,
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
    period_key: str,
    canonical_name: str,
    statement_type: StatementType,
    terms: tuple[tuple[str, Decimal], ...],
    *,
    optional_zero_names: set[str] | None = None,
    expense_names: set[str] | None = None,
) -> None:
    if canonical_name in bucket:
        return
    optional_zero_names = optional_zero_names or set()
    expense_names = expense_names or set()
    components: list[tuple[str, ExtractedFact, Decimal]] = []
    value = Decimal("0")
    for name, sign in terms:
        fact = bucket.get(name)
        if fact is None:
            if name in optional_zero_names:
                continue
            return
        component_value = abs(fact.value) if name in expense_names and sign < 0 else fact.value
        value += component_value * sign
        components.append((name, fact, sign))
    if not components:
        return
    first = components[0][1]
    confidence = min((fact.confidence for _, fact, _ in components), default=Decimal("0.70")) - Decimal("0.08")
    if confidence < Decimal("0.60"):
        confidence = Decimal("0.60")
    formula = " + ".join(f"{sign}*{name}" for name, _, sign in components)
    out.append(
        ExtractedFact(
            canonical_name=canonical_name,
            local_name=f"derived_{canonical_name}",
            label=f"Derived {canonical_name}",
            statement_type=statement_type,
            value=value,
            raw_value=str(value),
            unit=first.unit,
            currency=first.currency,
            period_key=period_key,
            period_start=first.period_start,
            period_end=first.period_end,
            fiscal_year=first.fiscal_year or artifact.fiscal_year,
            fiscal_period=first.fiscal_period or artifact.fiscal_period,
            scale=first.scale,
            market=Market.EU,
            accounting_standard=AccountingStandard.IFRS,
            taxonomy="eu_ifrs_pdf_table_derived",
            gaap_status="derived_from_reported_components",
            confidence=confidence,
            evidence=EvidenceRef(
                source_type="derived_reported_metric",
                source_id=f"derived:{canonical_name}",
                page_number=first.evidence.page_number,
                table_index=first.evidence.table_index,
                row_index=first.evidence.row_index,
                column_index=first.evidence.column_index,
                url=artifact.source_url,
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
    )
    bucket[canonical_name] = out[-1]
