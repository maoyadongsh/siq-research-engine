from __future__ import annotations

from typing import Any

from .contracts import financial_checks_contract, financial_data_contract
from .models import DbLoadPlan, ExtractionResult, LoadPlanRow, StatementType, ValidationResult
from .provenance import evidence_display_target
from .storage import artifact_file_layout, get_storage_profile
from .normalization import stable_slug


PDF2MD_COMPATIBLE_TABLES = [
    "documents",
    "company_filings",
    "parse_runs",
    "financial_statements",
    "financial_statement_items",
    "financial_key_metrics",
    "financial_checks",
    "evidence_citations",
]


def build_load_plan(extraction: ExtractionResult, validation: ValidationResult) -> DbLoadPlan:
    storage = get_storage_profile(extraction.market)
    filing_id = extraction.report_id or stable_slug("filing", extraction.market, extraction.company_id, extraction.ticker, extraction.period_end, extraction.report_type)
    parse_run_id = stable_slug("parse_run", extraction.artifact_id, extraction.rule_version)
    file_layout = artifact_file_layout(
        market=extraction.market,
        company_name=extraction.company_name,
        ticker=extraction.ticker,
        report_type=extraction.report_type,
        report_form=extraction.report_form,
        artifact_id=extraction.artifact_id,
    )

    rows: list[LoadPlanRow] = [
        LoadPlanRow(
            table="financial_data_artifacts",
            operation="upsert",
            row={
                "artifact_id": extraction.artifact_id,
                "market": extraction.market.value,
                "company_id": extraction.company_id,
                "ticker": extraction.ticker,
                "report_id": extraction.report_id,
                "rule_version": extraction.rule_version,
                "profile_id": extraction.profile_id,
                "industry_profile": extraction.industry_profile,
                "payload": financial_data_contract(extraction),
            },
        ),
        LoadPlanRow(
            table="financial_checks_artifacts",
            operation="upsert",
            row={
                "artifact_id": extraction.artifact_id,
                "market": extraction.market.value,
                "company_id": extraction.company_id,
                "ticker": extraction.ticker,
                "rule_version": validation.rule_version,
                "profile_id": validation.profile_id,
                "overall_status": validation.overall_status.value,
                "payload": financial_checks_contract(validation),
            },
        ),
    ]

    rows.extend(_fact_rows(extraction))
    rows.extend(_validation_rows(validation, extraction.artifact_id))
    rows.extend(_evidence_rows(extraction, parse_run_id))

    return DbLoadPlan(
        target_database=storage.postgres_database,
        target_schema=storage.postgres_schema,
        wiki_namespace=storage.wiki_namespace,
        file_layout=file_layout,
        agent_policy=storage.agent_policy,
        compatible_pdf2md_tables=PDF2MD_COMPATIBLE_TABLES,
        artifact_id=extraction.artifact_id,
        market=extraction.market,
        company_id=extraction.company_id,
        ticker=extraction.ticker,
        report_id=extraction.report_id,
        parse_run_id=parse_run_id,
        filing_id=filing_id,
        rows=rows,
        warnings=list(extraction.warnings) + list(validation.warnings) + list(storage.notes),
    )


def _fact_rows(extraction: ExtractionResult) -> list[LoadPlanRow]:
    rows: list[LoadPlanRow] = []
    for statement in extraction.statements:
        rows.append(
            LoadPlanRow(
                table="financial_statements",
                operation="delete_then_insert",
                row={
                    "artifact_id": extraction.artifact_id,
                    "market": extraction.market.value,
                    "statement_id": statement.statement_id,
                    "statement_type": statement.statement_type.value,
                    "statement_name": statement.statement_name,
                    "scope": statement.scope,
                    "unit": statement.unit,
                    "currency": statement.currency,
                    "scale": str(statement.scale),
                    "raw": statement.model_dump(mode="json"),
                },
            )
        )
        for index, fact in enumerate(statement.items, start=1):
            rows.append(_fact_row("financial_facts", extraction.artifact_id, fact, index))
    for index, fact in enumerate(extraction.key_metrics, start=1):
        rows.append(_fact_row("financial_facts", extraction.artifact_id, fact, index))
    for index, fact in enumerate(extraction.operating_metrics, start=1):
        rows.append(_fact_row("operating_metric_facts", extraction.artifact_id, fact, index))
    return rows


def _fact_row(table: str, artifact_id: str, fact: Any, index: int) -> LoadPlanRow:
    return LoadPlanRow(
        table=table,
        operation="delete_then_insert",
        row={
            "artifact_id": artifact_id,
            "market": fact.market.value,
            "fact_index": index,
            "statement_type": fact.statement_type.value,
            "canonical_name": fact.canonical_name,
            "local_name": fact.local_name,
            "period_key": fact.period_key,
            "period_start": fact.period_start.isoformat() if fact.period_start else None,
            "period_end": fact.period_end.isoformat() if fact.period_end else None,
            "duration_days": fact.duration_days,
            "frame": fact.frame,
            "qtd_ytd_type": fact.qtd_ytd_type,
            "value": str(fact.value),
            "raw_value": fact.raw_value,
            "unit": fact.unit,
            "currency": fact.currency,
            "scale": str(fact.scale),
            "accounting_standard": fact.accounting_standard.value,
            "taxonomy": fact.taxonomy,
            "is_extension": fact.is_extension,
            "gaap_status": fact.gaap_status,
            "source_accession": fact.source_accession,
            "confidence": str(fact.confidence),
            "evidence": fact.evidence.model_dump(mode="json"),
            "evidence_target": evidence_display_target(fact.evidence),
            "raw": fact.raw,
        },
    )


def _validation_rows(validation: ValidationResult, artifact_id: str) -> list[LoadPlanRow]:
    return [
        LoadPlanRow(
            table="validation_checks",
            operation="delete_then_insert",
            row={
                "artifact_id": artifact_id,
                "market": validation.market.value,
                "check_index": index,
                "rule_id": check.rule_id,
                "rule_name": check.rule_name,
                "statement_type": check.statement_type.value if hasattr(check.statement_type, "value") else check.statement_type,
                "status": check.status.value,
                "period": check.period,
                "diff": str(check.diff) if check.diff is not None else None,
                "tolerance": str(check.tolerance) if check.tolerance is not None else None,
                "inputs": check.inputs,
                "left_side": check.left,
                "right_side": check.right,
                "evidence": [item.model_dump(mode="json") for item in check.evidence],
                "raw": check.raw,
            },
        )
        for index, check in enumerate(validation.checks, start=1)
    ]


def _evidence_rows(extraction: ExtractionResult, parse_run_id: str) -> list[LoadPlanRow]:
    rows: list[LoadPlanRow] = []
    facts = [item for statement in extraction.statements for item in statement.items]
    facts.extend(extraction.key_metrics)
    facts.extend(extraction.operating_metrics)
    for index, fact in enumerate(facts, start=1):
        evidence = fact.evidence
        rows.append(
            LoadPlanRow(
                table="evidence_citations",
                operation="delete_then_insert",
                row={
                    "citation_id": stable_slug("cite", extraction.artifact_id, fact.canonical_name, fact.period_key, index),
                    "artifact_id": extraction.artifact_id,
                    "parse_run_id": parse_run_id,
                    "market": extraction.market.value,
                    "source_type": evidence.source_type,
                    "source_id": evidence.source_id,
                    "page_number": evidence.page_number,
                    "rendered_page_number": evidence.rendered_page_number,
                    "section": evidence.section,
                    "anchor": evidence.anchor,
                    "xpath": evidence.xpath,
                    "xbrl_tag": evidence.xbrl_tag,
                    "accession_number": evidence.accession_number,
                    "quote_text": evidence.quote_text or evidence.html_snippet,
                    "url": evidence.url,
                    "path": evidence.path,
                    "target": evidence_display_target(evidence),
                    "raw": evidence.raw,
                },
            )
        )
    return rows
