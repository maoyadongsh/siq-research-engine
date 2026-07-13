from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from siq_market_contracts.financial_value_polarity import canonical_value_polarity

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
    table_period_key,
    tables_from_document_full,
)
from .rules import find_hk_rule


def resolve_hk_currency(
    *,
    unit: str | None,
    declared_currency: str | None,
    title: str | None = None,
    fallback: str | None = None,
) -> str | None:
    """Resolve HK reporting currency from the most specific source evidence."""
    return (
        infer_currency(unit, default=None)
        or infer_currency(title, default=None)
        or infer_currency(declared_currency, default=None)
        or infer_currency(fallback, default=fallback)
    )


def extract_artifact(artifact: ParsedArtifact) -> ExtractionResult:
    profile = get_profile(Market.HK)
    tables = list(artifact.tables) or tables_from_document_full(artifact.document_full)

    extracted: list[ExtractedFact] = []
    operating: list[ExtractedFact] = []
    warnings: list[str] = []
    seen: set[tuple[str, str, int | None, int, int]] = set()
    for table in tables:
        detected_statement_type = detect_table_statement_type(table)
        if _is_non_group_statement_table(table, detected_statement_type):
            continue
        period_columns = _period_columns_for_table(table, artifact, detected_statement_type)
        table_unit = table.unit or artifact.unit
        table_currency = resolve_hk_currency(
            unit=table_unit,
            declared_currency=table.currency,
            title=table.title,
            fallback=artifact.currency,
        )
        unit_currency = infer_currency(table_unit, default=None)
        declared_currency = infer_currency(table.currency, default=None)
        currency_conflict = bool(unit_currency and declared_currency and unit_currency != declared_currency)
        if currency_conflict:
            warnings.append(
                f"HK table {table.table_id} currency conflict resolved from explicit unit: "
                f"{declared_currency} -> {unit_currency}."
            )
        scale = infer_scale(table_unit)
        pending_section_rule: Any | None = None
        pending_section_label: str | None = None
        for row_index, row in enumerate(table.rows):
            if len(row) < 2:
                continue
            if row_index in period_columns.header_rows:
                continue
            label = _row_label(row, period_columns.label_columns)
            rule = find_hk_rule(label)
            numeric_cells = _numeric_cells_for_periods(row, period_columns.column_periods)
            if not numeric_cells and rule:
                pending_section_rule = rule
                pending_section_label = label
                continue
            if (
                numeric_cells
                and pending_section_rule is not None
                and (
                    not label
                    or (
                        detected_statement_type == StatementType.CASH_FLOW_STATEMENT
                        and _is_cash_flow_section_subtotal_label(label)
                    )
                )
            ):
                rule = pending_section_rule
                label = pending_section_label or pending_section_rule.canonical_name
                pending_section_rule = None
                pending_section_label = None
            elif rule and numeric_cells:
                if not (
                    detected_statement_type == StatementType.CASH_FLOW_STATEMENT
                    and pending_section_rule is not None
                    and pending_section_rule.statement_type == StatementType.CASH_FLOW_STATEMENT
                ):
                    pending_section_rule = None
                    pending_section_label = None
            if not rule:
                continue
            if (
                detected_statement_type
                and rule.statement_type != detected_statement_type
                and rule.statement_type != StatementType.KEY_METRICS
                and not _allow_cross_statement_fact(rule, label, detected_statement_type)
            ):
                continue
            for column_index, value in numeric_cells:
                value = _normalize_statement_value(rule.canonical_name, value)
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
                            "currency_resolution": {
                                "declared_currency": declared_currency,
                                "unit_currency": unit_currency,
                                "resolved_currency": table_currency,
                                "policy": "explicit_unit_then_title_then_declared_then_report_default",
                                "conflict": currency_conflict,
                            },
                        },
                    )
                )

        if detected_statement_type is None:
            operating.extend(extract_operating_metrics_from_tables(artifact, [table], confidence=Decimal("0.76")))

    if not extracted:
        warnings.append("No mapped HKEX/PDF table rows were extracted. Check table parsing quality or add issuer-specific aliases.")

    extracted = _prefer_hk_primary_cash_flow_rows(extracted)
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
        from_raw = _drop_hk_duplicate_period_columns(table, from_raw)
        return _PeriodColumns(from_raw, set(), label_columns)

    month_day_hint = _month_day_hint_for_table(table)
    best_row_index: int | None = None
    best_periods: dict[int, str] = {}
    max_columns = max((len(row) for row in table.rows), default=1)
    for row_index, row in enumerate(table.rows[:4]):
        aligned_row = _aligned_header_row(row, max_columns, label_columns)
        periods: dict[int, str] = {}
        for column_index, cell in enumerate(aligned_row):
            if column_index < label_columns or _is_note_column(cell):
                continue
            parsed = _period_from_header_cell(cell, artifact, statement_type, month_day_hint)
            if parsed:
                periods[column_index] = parsed
        if len(periods) > len(best_periods):
            best_row_index = row_index
            best_periods = periods

    if best_periods:
        best_periods = _drop_hk_duplicate_period_columns(table, best_periods)
        return _PeriodColumns(best_periods, {best_row_index} if best_row_index is not None else set(), label_columns)

    fallback = table_period_key(artifact, table)
    header = _aligned_header_row(table.rows[0], max_columns, label_columns) if table.rows else []
    periods = {
        column_index: fallback
        for column_index in range(label_columns, max_columns)
        if not _is_note_column(_cell_at(header, column_index))
    }
    periods = _drop_hk_duplicate_period_columns(table, periods)
    return _PeriodColumns(periods, set(), label_columns)


def _drop_hk_duplicate_period_columns(table: ParsedTable, column_periods: dict[int, str]) -> dict[int, str]:
    column_periods = _drop_hk_convenience_translation_columns(table, column_periods)
    if not column_periods:
        return column_periods
    by_period: dict[str, list[int]] = {}
    for column_index, period in column_periods.items():
        by_period.setdefault(period, []).append(column_index)
    keep = set(column_periods)
    for columns in by_period.values():
        if len(columns) < 2:
            continue
        total_columns = [column for column in columns if _looks_like_total_period_column(table, column)]
        if len(total_columns) == 1:
            keep.difference_update(column for column in columns if column != total_columns[0])
    return {column_index: period for column_index, period in column_periods.items() if column_index in keep}


def _drop_hk_convenience_translation_columns(table: ParsedTable, column_periods: dict[int, str]) -> dict[int, str]:
    if not column_periods:
        return column_periods
    by_period: dict[str, list[int]] = {}
    for column_index, period in column_periods.items():
        by_period.setdefault(period, []).append(column_index)
    drop: set[int] = set()
    for columns in by_period.values():
        if len(columns) < 2:
            continue
        primary_columns = [
            column
            for column in columns
            if not _looks_like_usd_convenience_column(table, column)
            and not _looks_like_percentage_convenience_column(table, column)
        ]
        if primary_columns:
            drop.update(column for column in columns if column not in primary_columns)
    if not drop:
        return column_periods
    return {column_index: period for column_index, period in column_periods.items() if column_index not in drop}


def _looks_like_usd_convenience_column(table: ParsedTable, column_index: int) -> bool:
    cells = [_cell_at(row, column_index) for row in table.rows[:5]]
    raw_text = " ".join(str(cell or "") for cell in cells).lower()
    text = compact_label(raw_text)
    return "us$" in raw_text or "usd" in text or "usnote" in text or bool(re.search(r"20\d{2}us", text))


def _looks_like_percentage_convenience_column(table: ParsedTable, column_index: int) -> bool:
    for cell in (_cell_at(row, column_index) for row in table.rows[:4]):
        raw = str(cell or "").strip().lower()
        text = compact_label(raw)
        if raw == "%" or text in {"percent", "percentage", "pct"}:
            return True
    return False


def _looks_like_total_period_column(table: ParsedTable, column_index: int) -> bool:
    cells = [_cell_at(row, column_index) for row in table.rows[:6]]
    text = compact_label(" ".join(str(cell or "") for cell in cells))
    if not text:
        return False
    if "total" not in text and "總計" not in text and "总计" not in text and "合計" not in text and "合计" not in text:
        return False
    return not any(token in text for token in ("subtotal", "currenttotal", "noncurrenttotal"))


def _label_column_count(rows: list[list[Any]]) -> int:
    bilingual_rows = 0
    for row in rows[:40]:
        if len(row) < 4:
            continue
        first = str(row[0] or "").strip()
        second = str(row[1] or "").strip()
        if not first or not second or _is_note_column(second):
            continue
        if compact_label(first) == compact_label(second):
            continue
        if _is_note_reference_cell(second):
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


def _is_non_group_statement_table(table: ParsedTable, statement_type: StatementType | None) -> bool:
    raw = table.raw if isinstance(table.raw, dict) else {}
    parts = [str(table.title or ""), str(raw.get("heading") or ""), str(raw.get("preview") or "")[:300]]
    for value in raw.get("source_caption") or []:
        parts.append(str(value))
    text = compact_label(" ".join(parts))
    if "consolidated" in text or "綜合" in text or "综合" in text:
        return False
    if any(
        token in text
        for token in (
            "statementoffinancialpositionofthecompany",
            "balancesheetofthecompany",
            "balancesheetofcompany",
            "companybalancesheet",
            "financialpositionofthecompany",
            "ofthecompany",
            "companylevel",
        )
    ):
        return True
    if statement_type != StatementType.BALANCE_SHEET:
        return False
    return any(
        token in text
        for token in (
            "balancesheetofinsurancemanufacturingsubsidiaries",
            "subsidiariesbytypeofcontract",
            "parentcompany",
            "subsidiary",
            "subsidiaries",
        )
    )


def _normalize_statement_value(canonical_name: str, value: Decimal) -> Decimal:
    magnitude_canonical = canonical_name in {
        "total_liabilities",
        "current_liabilities",
        "non_current_liabilities",
        "borrowings",
        "lease_liabilities",
        "contract_liabilities",
    } or canonical_value_polarity("HK", canonical_name) == "deduction_magnitude"
    if magnitude_canonical and value < 0:
        return abs(value)
    return value


def _prefer_hk_primary_cash_flow_rows(facts: list[ExtractedFact]) -> list[ExtractedFact]:
    grouped: dict[tuple[str, str, int | None, int | None], list[ExtractedFact]] = {}
    for fact in facts:
        if fact.canonical_name != "operating_cash_flow_net":
            continue
        grouped.setdefault(
            (
                fact.canonical_name,
                fact.period_key,
                fact.evidence.table_index,
                fact.evidence.column_index,
            ),
            [],
        ).append(fact)

    drop_ids: set[int] = set()
    for items in grouped.values():
        if not any(_is_primary_operating_cash_flow_label(item.local_name) for item in items):
            continue
        for item in items:
            if _is_pre_bridge_cash_generated_label(item.local_name):
                drop_ids.add(id(item))

    if not drop_ids:
        return facts
    return [fact for fact in facts if id(fact) not in drop_ids]


def _is_primary_operating_cash_flow_label(value: Any) -> bool:
    normalized = compact_label(value)
    return (
        "operatingactivities" in normalized
        and (
            normalized.startswith("netcash")
            or "netcashflow" in normalized
            or "netcashflows" in normalized
            or "cashflowsgeneratedfrom" in normalized
            or "cashflowsusedin" in normalized
        )
    )


def _is_cash_flow_section_subtotal_label(value: Any) -> bool:
    normalized = compact_label(value)
    return normalized in {"subtotal", "小計", "小计", "小計", "小计"} or normalized.startswith(("subtotal", "小計", "小计"))


def _is_pre_bridge_cash_generated_label(value: Any) -> bool:
    normalized = compact_label(value)
    return normalized in {"cashgeneratedfromoperations", "netcashgeneratedfromoperations"} or normalized.startswith(
        ("cashgeneratedfromoperations", "netcashgeneratedfromoperations")
    )


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


def _period_from_header_cell(
    cell: Any,
    artifact: ParsedArtifact,
    statement_type: StatementType | None,
    month_day_hint: tuple[int, int] | None = None,
) -> str | None:
    text = str(cell or "").strip()
    parsed = _safe_parse_date(text)
    if parsed:
        return parsed.isoformat()
    year_match = re.search(r"(20\d{2}|19\d{2})", text)
    if not year_match:
        return None
    year = int(year_match.group(1))
    if month_day_hint:
        month, day = month_day_hint
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            pass
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


_MONTHS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sep": 9,
    "sept": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}


def _month_day_hint_for_table(table: ParsedTable) -> tuple[int, int] | None:
    parts: list[str] = []
    if table.title:
        parts.append(str(table.title))
    for row in table.rows[:5]:
        parts.append(" ".join(str(cell or "") for cell in row))
    raw = table.raw if isinstance(table.raw, dict) else {}
    if raw.get("preview"):
        parts.append(str(raw.get("preview")))
    structure = raw.get("structure") if isinstance(raw.get("structure"), dict) else {}
    for value in structure.get("header_preview") or []:
        parts.append(str(value))
    return _month_day_from_text(" ".join(parts))


def _month_day_from_text(text: str) -> tuple[int, int] | None:
    if not text:
        return None
    lowered = text.lower()
    month_names = "|".join(sorted(_MONTHS, key=len, reverse=True))
    for pattern in (
        rf"\b({month_names})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b",
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_names})\.?\b",
    ):
        match = re.search(pattern, lowered)
        if match:
            first, second = match.groups()
            if first.isdigit():
                day = int(first)
                month = _MONTHS.get(second.rstrip("."))
            else:
                month = _MONTHS.get(first.rstrip("."))
                day = int(second)
            if month and 1 <= day <= 31:
                return month, day
    zh = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日號号]", text)
    if zh:
        month, day = (int(part) for part in zh.groups())
        if 1 <= month <= 12 and 1 <= day <= 31:
            return month, day
    return None


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
    text = compact_label(cell)
    return text in {"note", "notes", "附注", "附註"} or "notes" == text


def _is_note_reference_cell(cell: Any) -> bool:
    text = str(cell or "").strip()
    if not text:
        return False
    return bool(re.fullmatch(r"(?:note\s*)?\d+[A-Za-z]?(?:\([A-Za-z0-9]+\))*", text, flags=re.I))


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
            (("parent_equity", Decimal("1")), ("nci_equity", Decimal("1"))),
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
