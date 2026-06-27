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
    ParsedTable,
    StatementType,
)
from ...normalization import compact_label, infer_currency, infer_scale, parse_date, parse_decimal
from ...registry import get_profile
from ...statement_detection import detect_table_statement_type
from ..common import (
    build_result,
    extract_operating_metrics_from_tables,
    first_numeric_cell,
    table_period_key,
    tables_from_document_full,
)
from .rules import find_hk_rule


def extract_artifact(artifact: ParsedArtifact) -> ExtractionResult:
    profile = get_profile(Market.HK)
    tables = list(artifact.tables) or tables_from_document_full(artifact.document_full)

    extracted: list[ExtractedFact] = []
    operating: list[ExtractedFact] = []
    warnings: list[str] = []
    seen: set[tuple[str, str, int | None, int, int]] = set()
    for table in tables:
        detected_statement_type = detect_table_statement_type(table)
        period_columns = _period_columns_for_table(table, artifact, detected_statement_type)
        table_unit = table.unit or artifact.unit
        table_currency = infer_currency(table.currency, table.unit, table.title, artifact.currency, default=artifact.currency)
        scale = infer_scale(table_unit)
        for row_index, row in enumerate(table.rows):
            if len(row) < 2:
                continue
            if row_index in period_columns.header_rows:
                continue
            label = _row_label(row, period_columns.label_columns)
            rule = find_hk_rule(label)
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
                        market=Market.HK,
                        accounting_standard=_accounting_standard(artifact),
                        taxonomy="hkex_pdf_table",
                        gaap_status="reported_gaap",
                        confidence=Decimal("0.84") if detected_statement_type else Decimal("0.78"),
                        evidence=EvidenceRef(
                            source_type="pdf_statement_table" if detected_statement_type else "pdf_table",
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

        if detected_statement_type is None:
            operating.extend(extract_operating_metrics_from_tables(artifact, [table], confidence=Decimal("0.76")))

    if not extracted:
        warnings.append("No mapped HKEX/PDF table rows were extracted. Check table parsing quality or add issuer-specific aliases.")

    derived = _derive_missing_facts(extracted, artifact)
    if derived:
        extracted.extend(derived)
        warnings.append(f"Derived {len(derived)} HK metrics from reported component facts.")

    return build_result(artifact, profile.profile_id, profile.rule_version, extracted, operating, warnings)


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
    for row_index, row in enumerate(table.rows[:4]):
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


def _label_column_count(rows: list[list[Any]]) -> int:
    bilingual_rows = 0
    for row in rows[:40]:
        if len(row) < 4:
            continue
        first = str(row[0] or "").strip()
        second = str(row[1] or "").strip()
        if not first or not second or _is_note_column(second):
            continue
        if parse_decimal(second) is not None or _looks_like_period_header(second):
            continue
        if re.search(r"[A-Za-z]", second):
            bilingual_rows += 1
    return 2 if bilingual_rows >= 2 else 1


def _row_label(row: list[Any], label_columns: int) -> str:
    parts = [str(cell or "").strip() for cell in row[:label_columns]]
    label = " ".join(part for part in parts if part)
    return label or str(row[0] or "").strip()


def _aligned_header_row(row: list[Any], max_columns: int, label_columns: int) -> list[Any]:
    aligned = list(row)
    if label_columns > 1 and len(aligned) < max_columns:
        missing = max_columns - len(aligned)
        aligned = aligned[:1] + [""] * missing + aligned[1:]
    if len(aligned) < max_columns:
        aligned.extend([""] * (max_columns - len(aligned)))
    return aligned


def _looks_like_period_header(value: Any) -> bool:
    text = str(value or "")
    return bool(re.search(r"(20\d{2}|19\d{2})", text))


def _allow_cross_statement_fact(rule: Any, label: str, detected_statement_type: StatementType) -> bool:
    if detected_statement_type != StatementType.INCOME_STATEMENT or rule.statement_type != StatementType.BALANCE_SHEET:
        return False
    normalized = compact_label(label)
    return normalized in {
        "totalassets",
        "totalasset",
        "資產總額",
        "资产总额",
        "totalliabilities",
        "totalliability",
        "負債總額",
        "负债总额",
        "netassets",
        "netasset",
        "資產淨值",
        "资产净值",
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
    return text in {"note", "notes", "附注", "附註"} or "notes" == text


def _cell_at(row: list[Any], index: int) -> Any:
    return row[index] if index < len(row) else None


def _year_from_period(period: str) -> int | None:
    match = re.match(r"(\d{4})", period or "")
    return int(match.group(1)) if match else None


def _accounting_standard(artifact: ParsedArtifact) -> AccountingStandard:
    if artifact.accounting_standard != AccountingStandard.UNKNOWN:
        return artifact.accounting_standard
    text = " ".join(str(value) for value in artifact.metadata.values()).lower()
    if "casbe" in text or "china accounting standards" in text or "prc accounting standards" in text or "中国企业会计准则" in text:
        return AccountingStandard.CASBE
    if "ifrs" in text:
        return AccountingStandard.IFRS
    return AccountingStandard.HKFRS


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
            "net_profit",
            StatementType.INCOME_STATEMENT,
            (("parent_net_profit", Decimal("1")), ("nci_profit", Decimal("1"))),
        )
        _derive_one(
            derived,
            bucket,
            artifact,
            period_key,
            "parent_net_profit",
            StatementType.INCOME_STATEMENT,
            (("net_profit", Decimal("1")), ("nci_profit", Decimal("-1"))),
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
            market=Market.HK,
            accounting_standard=_accounting_standard(artifact),
            taxonomy="hkex_pdf_table_derived",
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
