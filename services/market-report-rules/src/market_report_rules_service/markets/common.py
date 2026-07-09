from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from ..models import (
    EvidenceRef,
    ExtractedFact,
    ExtractionResult,
    FinancialStatement,
    ParsedArtifact,
    ParsedTable,
    StatementType,
)
from ..normalization import infer_currency, infer_scale, parse_date, parse_decimal, period_key
from ..operating_metrics import find_operating_metric_rule


STATEMENT_NAMES = {
    StatementType.BALANCE_SHEET: "Balance Sheet",
    StatementType.INCOME_STATEMENT: "Income Statement",
    StatementType.CASH_FLOW_STATEMENT: "Cash Flow Statement",
    StatementType.KEY_METRICS: "Key Metrics",
}


def build_result(
    artifact: ParsedArtifact,
    profile_id: str,
    rule_version: str,
    facts: list[ExtractedFact],
    operating_metrics: list[ExtractedFact],
    warnings: list[str],
) -> ExtractionResult:
    statements = _group_statements(facts)
    key_metrics = [fact for fact in facts if fact.statement_type == StatementType.KEY_METRICS]
    return ExtractionResult(
        rule_version=rule_version,
        profile_id=profile_id,
        artifact_id=artifact.artifact_id,
        market=artifact.market,
        accounting_standard=artifact.accounting_standard,
        industry_profile=artifact.industry_profile,
        company_overrides=artifact.company_overrides,
        company_id=artifact.company_id,
        ticker=artifact.ticker,
        company_name=artifact.company_name,
        report_id=artifact.report_id,
        report_type=artifact.report_type,
        report_form=artifact.report_form,
        fiscal_year=artifact.fiscal_year,
        fiscal_period=artifact.fiscal_period,
        period_end=artifact.period_end,
        statements=statements,
        key_metrics=key_metrics,
        operating_metrics=operating_metrics,
        warnings=warnings,
    )


def table_period_key(artifact: ParsedArtifact, table: ParsedTable) -> str:
    raw_period = table.raw.get("period_end") if isinstance(table.raw, dict) else None
    return period_key(parse_date(raw_period) or artifact.period_end, artifact.fiscal_year)


def first_numeric_cell(cells: list[Any]) -> tuple[Decimal | None, int | None]:
    for index, cell in enumerate(cells):
        value = parse_decimal(cell)
        if value is not None:
            return value, index
    return None, None


def tables_from_document_full(document_full: dict[str, Any]) -> list[ParsedTable]:
    tables: list[ParsedTable] = []
    candidates = (
        document_full.get("tables")
        or (document_full.get("content_list_enhanced") or {}).get("tables")
        or (document_full.get("quality_report") or {}).get("table_index")
        or []
    )
    if not isinstance(candidates, list):
        return tables
    for index, item in enumerate(candidates, start=1):
        if not isinstance(item, dict):
            continue
        rows = item.get("rows") or item.get("data") or item.get("table_rows")
        if not isinstance(rows, list):
            continue
        tables.append(
            ParsedTable(
                table_id=str(item.get("table_id") or item.get("table_index") or index),
                title=item.get("title") or item.get("heading"),
                rows=rows,
                page_number=item.get("pdf_page_number") or item.get("page_number"),
                table_index=item.get("table_index") or index,
                unit=item.get("unit"),
                currency=item.get("currency"),
                raw=item,
            )
        )
    return tables


def extract_operating_metrics_from_tables(
    artifact: ParsedArtifact,
    tables: list[ParsedTable],
    *,
    confidence: Decimal,
) -> list[ExtractedFact]:
    metrics: list[ExtractedFact] = []
    seen: set[tuple[str, str, int | None, int]] = set()
    for table in tables:
        table_unit = table.unit or artifact.unit
        table_currency = infer_currency(table.currency, table.unit, table.title, artifact.currency, default=artifact.currency)
        scale = infer_scale(table_unit)
        context_rule = None
        for row_index, row in enumerate(table.rows):
            if len(row) < 2:
                continue
            label = str(row[0] or "").strip()
            rule = find_operating_metric_rule(label, artifact.market, artifact.industry_profile)
            value, column_index = first_numeric_cell(row[1:])
            if rule and value is None:
                context_rule = rule
                continue
            if not rule:
                rule = context_rule
            if not rule:
                continue
            if value is None:
                continue
            if "non_negative" in rule.validation and value < 0:
                continue
            metric_period = table_period_key(artifact, table)
            key = (rule.canonical_name, metric_period, table.table_index, row_index)
            if key in seen:
                continue
            seen.add(key)
            metrics.append(
                ExtractedFact(
                    canonical_name=rule.canonical_name,
                    local_name=label,
                    label=label,
                    statement_type=StatementType.OPERATING_METRICS,
                    value=value,
                    raw_value=str(row[column_index + 1]) if column_index is not None and column_index + 1 < len(row) else None,
                    unit=table_unit,
                    currency=table_currency,
                    period_key=metric_period,
                    period_end=parse_date(metric_period),
                    fiscal_year=artifact.fiscal_year,
                    fiscal_period=artifact.fiscal_period,
                    scale=scale,
                    market=artifact.market,
                    accounting_standard=artifact.accounting_standard,
                    taxonomy="operating_kpi",
                    gaap_status="operating_kpi",
                    confidence=confidence,
                    evidence=EvidenceRef(
                        source_type="operating_kpi_table",
                        source_id=table.table_id,
                        page_number=table.page_number,
                        table_index=table.table_index,
                        row_index=row_index,
                        column_index=column_index + 1 if column_index is not None else None,
                        url=artifact.source_url,
                        quote_text=" | ".join(str(cell) for cell in row),
                        raw={"profile": rule.profile, "unit_kind": rule.unit_kind, "table": table.raw, "row": row},
                    ),
                    raw={
                        "table_id": table.table_id,
                        "row": row,
                        "operating_profile": rule.profile,
                        "unit_kind": rule.unit_kind,
                        "validation": list(rule.validation),
                    },
                )
            )
    return metrics


def _group_statements(facts: list[ExtractedFact]) -> list[FinancialStatement]:
    grouped: dict[StatementType, list[ExtractedFact]] = defaultdict(list)
    for fact in facts:
        if fact.statement_type not in {StatementType.KEY_METRICS, StatementType.OPERATING_METRICS}:
            grouped[fact.statement_type].append(fact)

    statements: list[FinancialStatement] = []
    for statement_type in (
        StatementType.BALANCE_SHEET,
        StatementType.INCOME_STATEMENT,
        StatementType.CASH_FLOW_STATEMENT,
    ):
        items = grouped.get(statement_type) or []
        if not items:
            continue
        table_indexes = sorted(
            {
                item.evidence.table_index
                for item in items
                if item.evidence and item.evidence.table_index is not None
            }
        )
        first = items[0]
        statements.append(
            FinancialStatement(
                statement_id=statement_type.value,
                statement_type=statement_type,
                statement_name=STATEMENT_NAMES[statement_type],
                scope="consolidated",
                scope_name="Consolidated",
                title=STATEMENT_NAMES[statement_type],
                unit=first.unit,
                scale=first.scale,
                currency=first.currency,
                table_indexes=table_indexes,
                columns=_columns_for_items(items),
                items=items,
            )
        )
    return statements


def _columns_for_items(items: list[ExtractedFact]) -> list[dict[str, Any]]:
    return [
        {"period_key": period, "label": period}
        for period in sorted({item.period_key for item in items})
    ]
