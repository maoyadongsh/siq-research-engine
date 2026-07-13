from __future__ import annotations

from collections import defaultdict
from typing import Any

from .models import ExtractedFact, ExtractionResult, StatementType, ValidationResult
from .provenance import evidence_display_target


def financial_data_contract(extraction: ExtractionResult) -> dict[str, Any]:
    statements = []
    for statement in extraction.statements:
        statements.append(
            {
                "statement_id": statement.statement_id,
                "statement_type": statement.statement_type.value,
                "statement_name": statement.statement_name,
                "scope": statement.scope,
                "scope_name": statement.scope_name,
                "title": statement.title,
                "unit": statement.unit,
                "scale": str(statement.scale),
                "currency": statement.currency,
                "table_indexes": statement.table_indexes,
                "columns": statement.columns,
                "items": _group_items(statement.items),
            }
        )

    key_metrics = _group_items(extraction.key_metrics, metric_mode=True)
    operating_metrics = _group_items(extraction.operating_metrics, metric_mode=True)
    all_facts = [item for statement in extraction.statements for item in statement.items]
    all_facts.extend(extraction.key_metrics)
    all_facts.extend(extraction.operating_metrics)

    return {
        "schema_version": extraction.schema_version,
        "rule_version": extraction.rule_version,
        "profile_id": extraction.profile_id,
        "market": extraction.market.value,
        "artifact_id": extraction.artifact_id,
        "company_id": extraction.company_id,
        "ticker": extraction.ticker,
        "company_name": extraction.company_name,
        "report_id": extraction.report_id,
        "report_type": extraction.report_type,
        "report_form": extraction.report_form,
        "report_kind": extraction.report_type,
        "report_year": extraction.fiscal_year,
        "fiscal_year": extraction.fiscal_year,
        "fiscal_period": extraction.fiscal_period,
        "period_end": extraction.period_end.isoformat() if extraction.period_end else None,
        "accounting_standard": extraction.accounting_standard.value,
        "industry_profile": extraction.industry_profile,
        "company_overrides": extraction.company_overrides,
        "statements": statements,
        "key_metrics": key_metrics,
        "operating_metrics": operating_metrics,
        "summary": {
            "statement_count": len(statements),
            "statement_item_count": sum(len(statement["items"]) for statement in statements),
            "key_metric_count": len(key_metrics),
            "operating_metric_count": len(operating_metrics),
            "evidence_count": sum(1 for fact in all_facts if fact.evidence),
        },
        "warnings": extraction.warnings,
        "generated_at": extraction.generated_at.isoformat(),
    }


def financial_checks_contract(validation: ValidationResult) -> dict[str, Any]:
    return {
        "schema_version": validation.schema_version,
        "rule_version": validation.rule_version,
        "profile_id": validation.profile_id,
        "market": validation.market.value,
        "artifact_id": validation.artifact_id,
        "industry_profile": validation.industry_profile,
        "overall_status": validation.overall_status.value,
        "summary": validation.summary,
        "checks": [
            {
                "rule_id": check.rule_id,
                "rule_name": check.rule_name,
                "statement_type": check.statement_type.value if hasattr(check.statement_type, "value") else check.statement_type,
                "scope": check.scope,
                "period": check.period,
                "status": check.status.value,
                "diff": str(check.diff) if check.diff is not None else None,
                "tolerance": str(check.tolerance) if check.tolerance is not None else None,
                "inputs": check.inputs,
                "left": check.left,
                "right": check.right,
                "reason": check.reason,
                "evidence": [item.model_dump(mode="json") for item in check.evidence],
                "evidence_targets": [evidence_display_target(item) for item in check.evidence],
                "raw": check.raw,
            }
            for check in validation.checks
        ],
        "warnings": validation.warnings,
        "advisories": validation.advisories,
        "generated_at": validation.generated_at.isoformat(),
    }


def _group_items(facts: list[ExtractedFact], metric_mode: bool = False) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[ExtractedFact]] = defaultdict(list)
    for fact in facts:
        key = (fact.canonical_name, fact.local_name, fact.statement_type.value)
        buckets[key].append(fact)

    rows = []
    for index, ((canonical_name, local_name, statement_type), items) in enumerate(sorted(buckets.items()), start=1):
        first = max(items, key=lambda item: item.confidence)
        values = {item.period_key: str(item.value) for item in items}
        raw_values = {item.period_key: item.raw_value for item in items}
        sources = {item.period_key: item.evidence.model_dump(mode="json") for item in items}
        row = {
            "metric_index" if metric_mode else "item_index": index,
            "name": first.label or local_name,
            "canonical_name": canonical_name,
            "statement_type": statement_type,
            "values": values,
            "raw_values": raw_values,
            "sources": sources,
            "evidence_targets": {item.period_key: evidence_display_target(item.evidence) for item in items},
            "unit": first.unit,
            "currency": first.currency,
            "scale": str(first.scale),
            "periods": {
                item.period_key: {
                    "period_start": item.period_start.isoformat() if item.period_start else None,
                    "period_end": item.period_end.isoformat() if item.period_end else None,
                    "duration_days": item.duration_days,
                    "frame": item.frame,
                    "qtd_ytd_type": item.qtd_ytd_type,
                    "fiscal_year": item.fiscal_year,
                    "fiscal_period": item.fiscal_period,
                }
                for item in items
            },
            "taxonomy": first.taxonomy,
            "is_extension": first.is_extension,
            "gaap_status": first.gaap_status,
            "source_accession": first.source_accession,
            "confidence": str(first.confidence),
            "raw": [item.raw for item in items],
        }
        if first.statement_type == StatementType.OPERATING_METRICS:
            row["operating_profile"] = first.raw.get("operating_profile")
            row["unit_kind"] = first.raw.get("unit_kind")
        rows.append(row)
    return rows
