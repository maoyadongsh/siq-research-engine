from __future__ import annotations

from typing import Any

from .contracts import financial_checks_contract, financial_data_contract
from .models import CheckStatus, DbLoadPlan, ExtractionResult, LoadPlanRow, PromotionDecision, StatementType, ValidationResult
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
    "evidence_resolvability_audits",
]
PROMOTION_TARGETS = ("draft", "review", "canonical", "retrieval", "production")
DECISION_RANK = {"allow": 0, "review": 1, "block": 2}
SEVERITY_RANK = {"observe": 0, "soft": 1, "hard": 2}


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

    artifact_rows: list[LoadPlanRow] = [
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

    audit_rows = _validation_rows(validation, extraction.artifact_id)
    audit_rows.extend(_evidence_resolvability_audit_rows(validation, extraction.artifact_id))
    canonical_rows: list[LoadPlanRow] = []
    canonical_rows.extend(_fact_rows(extraction))
    canonical_rows.extend(_evidence_rows(extraction, parse_run_id))
    promotion_decisions = _promotion_decisions(validation)
    can_import = promotion_decisions["canonical"].decision == "allow"
    can_vector_ingest = promotion_decisions["retrieval"].decision == "allow"
    blocked_reasons = _blocked_reasons(promotion_decisions)
    rows = artifact_rows + audit_rows + (canonical_rows if can_import else [])
    quarantine_rows = [] if can_import else canonical_rows

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
        can_import=can_import,
        can_vector_ingest=can_vector_ingest,
        promotion_decisions=promotion_decisions,
        blocked_reasons=blocked_reasons,
        rows=rows,
        quarantine_rows=quarantine_rows,
        warnings=list(extraction.warnings) + list(validation.warnings) + list(storage.notes),
    )


def _promotion_decisions(validation: ValidationResult) -> dict[str, PromotionDecision]:
    decisions = _empty_promotion_decisions()
    for check in validation.checks:
        raw_decisions = check.raw.get("gate_decisions_by_target") if isinstance(check.raw, dict) else None
        if not isinstance(raw_decisions, dict):
            continue
        for target, payload in raw_decisions.items():
            if target not in decisions or not isinstance(payload, dict):
                continue
            _merge_promotion_decision(decisions[target], payload)
    _apply_overall_status_fallback(decisions, validation)
    return decisions


def _empty_promotion_decisions() -> dict[str, PromotionDecision]:
    return {
        target: PromotionDecision(target=target, promotion_target=target)
        for target in PROMOTION_TARGETS
    }


def _merge_promotion_decision(decision: PromotionDecision, payload: dict[str, Any]) -> None:
    next_decision = str(payload.get("decision") or "allow")
    next_severity = str(payload.get("severity") or "observe")
    if DECISION_RANK.get(next_decision, 0) > DECISION_RANK.get(decision.decision, 0):
        decision.decision = next_decision  # type: ignore[assignment]
    if SEVERITY_RANK.get(next_severity, 0) > SEVERITY_RANK.get(decision.severity, 0):
        decision.severity = next_severity  # type: ignore[assignment]
    decision.rule_ids = _append_unique(decision.rule_ids, payload.get("rule_ids"))
    decision.review_rule_ids = _append_unique(decision.review_rule_ids, payload.get("review_rule_ids"))
    decision.blocking_rule_ids = _append_unique(decision.blocking_rule_ids, payload.get("blocking_rule_ids"))
    decision.reasons = _append_unique(decision.reasons, payload.get("reasons"))


def _apply_overall_status_fallback(decisions: dict[str, PromotionDecision], validation: ValidationResult) -> None:
    if validation.overall_status == CheckStatus.FAIL:
        fallback_decision = "block"
        fallback_severity = "hard"
    elif validation.overall_status == CheckStatus.WARNING:
        fallback_decision = "review"
        fallback_severity = "soft"
    else:
        return
    reasons = validation.warnings or [f"validation.overall_status.{validation.overall_status.value}"]
    rule_id = f"validation.overall_status.{validation.overall_status.value}"
    payload: dict[str, Any] = {
        "decision": fallback_decision,
        "severity": fallback_severity,
        "rule_ids": [rule_id],
        "reasons": reasons,
    }
    if fallback_decision == "block":
        payload["blocking_rule_ids"] = [rule_id]
    else:
        payload["review_rule_ids"] = [rule_id]
    for target in ("canonical", "retrieval", "production"):
        _merge_promotion_decision(decisions[target], payload)


def _append_unique(current: list[str], values: Any) -> list[str]:
    result = list(current)
    if isinstance(values, str):
        candidates = [values]
    elif isinstance(values, list):
        candidates = values
    else:
        candidates = []
    seen = set(result)
    for value in candidates:
        text = str(value or "").strip()
        if text and text not in seen:
            result.append(text)
            seen.add(text)
    return result


def _blocked_reasons(decisions: dict[str, PromotionDecision]) -> list[str]:
    reasons: list[str] = []
    for target in ("canonical", "retrieval", "production"):
        decision = decisions[target]
        if decision.decision == "allow":
            continue
        parts = decision.blocking_rule_ids or decision.review_rule_ids or decision.rule_ids or decision.reasons
        if not parts:
            reasons.append(f"{target}:{decision.decision}")
            continue
        reasons.extend(f"{target}:{decision.decision}:{part}" for part in parts)
    return _append_unique([], reasons)


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


def _evidence_resolvability_audit_rows(validation: ValidationResult, artifact_id: str) -> list[LoadPlanRow]:
    rows: list[LoadPlanRow] = []
    for index, check in enumerate(validation.checks, start=1):
        if check.rule_id != "package.quality_gates" or not isinstance(check.raw, dict):
            continue
        quality_gates = check.raw.get("quality_gates") if isinstance(check.raw.get("quality_gates"), dict) else {}
        raw_unresolvable = quality_gates.get("unresolvable_evidence") if isinstance(quality_gates, dict) else []
        unresolvable_refs = raw_unresolvable if isinstance(raw_unresolvable, list) else []
        rows.append(
            LoadPlanRow(
                table="evidence_resolvability_audits",
                operation="delete_then_insert",
                row={
                    "artifact_id": artifact_id,
                    "market": validation.market.value,
                    "audit_index": index,
                    "rule_id": check.rule_id,
                    "status": check.status.value,
                    "evidence_resolvability_ratio": check.raw.get("evidence_resolvability_ratio"),
                    "resolvable_evidence_count": quality_gates.get("resolvable_evidence_count"),
                    "unresolvable_evidence_count": check.raw.get("unresolvable_evidence_count"),
                    "unresolvable_refs": unresolvable_refs,
                    "quality_gates_raw": quality_gates,
                    "raw": check.raw,
                },
            )
        )
    return rows


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
