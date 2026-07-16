"""Adapter for PDF-derived CN/HK/JP/KR/EU report packages."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .base import (
    ADAPTER_CONTRACT_VERSION,
    AdapterContext,
    artifact_path,
    company_artifact_path,
    list_payload,
    normalize_evidence_ref,
    normalize_fact,
    read_json,
    read_text,
)


class PDFMarketAdapter:
    source_family = "pdf_market"
    adapter_name = "pdf_market"
    adapter_version = "1.0.0"

    def build(self, context: AdapterContext) -> dict[str, Any]:
        target = context.research_target
        identity = target["research_identity"]
        report = target["source_report"]
        report_id = str(report["report_id"])

        markdown_path = artifact_path(
            context,
            manifest_keys=("wiki_report_complete", "report_complete"),
            fallbacks=("report.md", "sections/report_complete.md", "sections/report.md", "parser/result_complete.md"),
        )
        document_path = artifact_path(
            context,
            manifest_keys=("document_full",),
            fallbacks=("document_full.json", "parser/document_full.json"),
            required=markdown_path is None,
        )
        report_markdown = read_text(markdown_path) if markdown_path else ""
        normalized_metrics_path = artifact_path(
            context,
            manifest_keys=("normalized_metrics",),
            fallbacks=("metrics/normalized_metrics.json",),
        )
        if normalized_metrics_path is None:
            normalized_metrics_path = _selected_company_artifact(
                context,
                (
                    f"metrics/reports/{report_id}/normalized_metrics.json",
                    f"metrics/reports/{report_id}/three_statements.json",
                    "metrics/latest/normalized_metrics.json",
                    "metrics/latest/three_statements.json",
                ),
                report_id=report_id,
            )
        metric_payload = read_json(normalized_metrics_path) if normalized_metrics_path else None
        raw_metrics = list_payload(metric_payload, "metrics", "data", "facts")
        if not raw_metrics:
            key_metrics_path = artifact_path(context, fallbacks=("metrics/key_metrics.json",))
            if key_metrics_path is None:
                key_metrics_path = _selected_company_artifact(
                    context,
                    (f"metrics/reports/{report_id}/key_metrics.json", "metrics/latest/key_metrics.json"),
                    report_id=report_id,
                )
            metric_payload = read_json(key_metrics_path) if key_metrics_path else None
            normalized_metrics_path = key_metrics_path
            raw_metrics = list_payload(metric_payload, "metrics", "data", "facts")

        source_map_path = artifact_path(
            context,
            manifest_keys=("source_map",),
            fallbacks=("qa/source_map.json",),
        )
        if source_map_path is None:
            source_map_path = _selected_company_artifact(
                context,
                (
                    f"evidence/reports/{report_id}/source_map.json",
                    f"evidence/reports/{report_id}/evidence_index.json",
                    f"evidence/reports/{report_id}/pdf_refs.json",
                    "evidence/source_map_latest.json",
                    "evidence/evidence_index.json",
                    "evidence/pdf_refs.json",
                ),
                report_id=report_id,
            )
        source_map = read_json(source_map_path) if source_map_path else None
        raw_evidence = list_payload(source_map, "entries", "evidence", "items")
        evidence_refs = [
            normalize_evidence_ref(
                item,
                identity=identity,
                report_id=report_id,
                source_family=self.source_family,
                default_kind="pdf_page",
            )
            for item in raw_evidence
        ]
        evidence_by_metric: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for raw, normalized in zip(raw_evidence, evidence_refs, strict=True):
            nested = raw.get("raw") if isinstance(raw.get("raw"), Mapping) else {}
            metric_key = str(raw.get("target") or raw.get("metric_key") or nested.get("metric_key") or "").strip()
            period = str(raw.get("period") or raw.get("period_end") or nested.get("period") or "").strip()
            if metric_key:
                evidence_by_metric.setdefault((metric_key, period), []).append(normalized)
        expanded_metrics = _expand_metric_records(raw_metrics)
        institution_kind = _financial_institution_kind(context, expanded_metrics)
        facts = []
        for raw in expanded_metrics:
            metric_key = str(raw.get("canonical_name") or raw.get("metric_key") or "").strip()
            period = str(raw.get("period_end") or raw.get("period") or "").strip()
            source = raw.get("source") if isinstance(raw.get("source"), Mapping) else {}
            refs = list(evidence_by_metric.get((metric_key, period), ()))
            if not refs:
                refs = list(evidence_by_metric.get((metric_key, ""), ()))
            source_ref = (
                normalize_evidence_ref(
                    {**source, "target": metric_key},
                    identity=identity,
                    report_id=report_id,
                    source_family=self.source_family,
                    default_kind="pdf_page",
                )
                if source and not refs
                else None
            )
            if source_ref:
                if source_ref["evidence_id"] not in {item["evidence_id"] for item in evidence_refs}:
                    evidence_refs.append(source_ref)
                refs.append(source_ref)
            normalized_raw = _apply_institution_metric_semantics(raw, institution_kind)
            facts.append(
                normalize_fact(
                    normalized_raw,
                    identity=identity,
                    report=report,
                    evidence_refs=refs,
                    source_family=self.source_family,
                )
            )

        financial_checks_path = artifact_path(
            context,
            manifest_keys=("financial_checks",),
            fallbacks=("metrics/financial_checks.json", "metrics/validation.json"),
        )
        if financial_checks_path is None:
            financial_checks_path = _selected_company_artifact(
                context,
                (
                    f"metrics/reports/{report_id}/financial_checks.json",
                    f"metrics/reports/{report_id}/validation.json",
                    "metrics/latest/financial_checks.json",
                    "metrics/latest/validation.json",
                ),
                report_id=report_id,
            )
        financial_checks = read_json(financial_checks_path) if financial_checks_path else {}
        quality_status = _financial_quality_status(financial_checks)
        normalization_warnings = list(
            dict.fromkeys(
                str(warning)
                for fact in facts
                for warning in fact.get("normalization_warnings") or ()
                if warning
            )
        )
        semantic_conflict_count = sum(fact.get("semantic_status") == "canonical_conflict" for fact in facts)
        return {
            "contract_version": ADAPTER_CONTRACT_VERSION,
            "adapter": {
                "name": self.adapter_name,
                "version": self.adapter_version,
                "source_family": self.source_family,
            },
            "inputs": _paths(
                report_markdown=markdown_path,
                document_full=document_path,
                normalized_metrics=normalized_metrics_path,
                financial_checks=financial_checks_path,
                source_map=source_map_path,
            ),
            "normalized_facts": facts,
            "evidence_refs": evidence_refs,
            "financial_checks": financial_checks or {},
            "normalization_warnings": normalization_warnings,
            "semantic_conflict_count": semantic_conflict_count,
            "financial_institution_policy": institution_kind,
            "quality_status": quality_status,
            "document_summary": {
                "markdown_char_count": len(report_markdown),
                "markdown_headings": _markdown_headings(report_markdown),
                "document_full_available": bool(document_path),
            },
            "capabilities": {
                "fulltext": bool(markdown_path or document_path),
                "structured_metrics": bool(facts),
                "evidence": bool(evidence_refs),
                "peer_metrics": False,
                "market_snapshot": False,
            },
            "degraded_reasons": [
                reason
                for condition, reason in (
                    (not facts, "structured_metrics_unavailable"),
                    (not evidence_refs, "evidence_map_unavailable"),
                )
                if condition
            ]
            + (["source_quality_warning"] if quality_status == "warning" else [])
            + (["metric_unit_semantics_warning"] if normalization_warnings else [])
            + (["financial_metric_canonical_conflict"] if semantic_conflict_count else []),
        }


def _paths(**values: Path | None) -> dict[str, str]:
    return {key: str(value) for key, value in values.items() if value is not None}


def _expand_metric_records(raw_metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    expanded: list[dict[str, Any]] = []
    for raw in raw_metrics:
        values = raw.get("values") if isinstance(raw.get("values"), Mapping) else None
        if not values:
            expanded.append(raw)
            continue
        raw_values = raw.get("raw_values") if isinstance(raw.get("raw_values"), Mapping) else {}
        sources = raw.get("sources") if isinstance(raw.get("sources"), Mapping) else {}
        for period, value in values.items():
            expanded.append(
                {
                    **raw,
                    "period_end": str(period),
                    "period": str(period),
                    "raw_value": raw_values.get(period, value),
                    # A-share key_metrics values are already base-currency
                    # normalized; scale is retained only for the raw label.
                    "normalized_value": value,
                    "source": sources.get(period) if isinstance(sources.get(period), Mapping) else {},
                }
            )
    return expanded


def _financial_institution_kind(
    context: AdapterContext,
    metrics: list[dict[str, Any]],
) -> str:
    company_metadata = read_json(context.company_dir / "company.json")
    company_metadata = company_metadata if isinstance(company_metadata, Mapping) else {}
    text = " ".join(
        str(value or "").lower()
        for value in (
            company_metadata.get("industry"),
            company_metadata.get("sector"),
            company_metadata.get("industry_profile"),
            company_metadata.get("company_type"),
            company_metadata.get("company_name"),
            company_metadata.get("company_short_name"),
            context.manifest.get("industry_profile"),
            context.manifest.get("company_name"),
            context.research_target.get("display_name"),
        )
    )
    keys = {
        str(item.get("canonical_name") or item.get("metric_key") or "").strip().lower()
        for item in metrics
    }
    if any(token in text for token in ("insurance", "insurer", "保险")):
        return "insurance"
    if any(token in text for token in ("bank", "banking", "银行")) or keys & {
        "net_interest_income",
        "net_interest_margin",
        "capital_adequacy_ratio",
        "core_tier_1_capital_adequacy_ratio",
    }:
        return "bank"
    return "general"


def _apply_institution_metric_semantics(raw: Mapping[str, Any], institution_kind: str) -> dict[str, Any]:
    output = dict(raw)
    if institution_kind not in {"bank", "insurance"}:
        return output
    metric_key = str(output.get("canonical_name") or output.get("metric_key") or "").strip().lower()
    raw_label = str(
        output.get("label")
        or output.get("metric_name")
        or output.get("local_name")
        or metric_key
    ).strip()
    normalized_label = " ".join(raw_label.lower().lstrip("-–— ").split())
    if institution_kind == "insurance" and metric_key == "operating_revenue" and _label_matches(
        normalized_label,
        ("insurance service revenue", "insurance revenue", "保险服务收入"),
    ):
        output["canonical_candidate"] = metric_key
        output["canonical_name"] = "insurance_revenue"
        output["metric_key"] = "insurance_revenue"
        output["semantic_status"] = "canonical_refined"
        output["core_metric_eligible"] = True
        return output
    policy = {
        "net_interest_income": ("net interest income", "净利息收入"),
        "net_interest_margin": ("net interest margin", "净息差"),
        "capital_adequacy_ratio": ("capital adequacy ratio", "资本充足率"),
        "core_tier_1_capital_adequacy_ratio": ("core tier 1 capital adequacy ratio", "核心一级资本充足率"),
        "total_assets": ("total assets", "资产总额", "总资产"),
        "total_liabilities": ("total liabilities", "负债总额", "总负债"),
        "total_equity": ("total equity", "shareholders equity", "股东权益", "权益总额"),
        "total_profit": ("profit before tax", "total profit", "利润总额", "税前利润"),
        "operating_profit": ("operating profit", "营业利润"),
        "insurance_revenue": ("insurance service revenue", "insurance revenue", "保险服务收入"),
        "solvency_ratio": ("solvency ratio", "偿付能力充足率"),
    }
    if metric_key in {"net_profit", "net_income", "parent_net_profit", "net_profit_parent"}:
        accepted = _net_profit_label_is_core(normalized_label, metric_key)
    elif metric_key == "operating_revenue" and institution_kind == "bank":
        accepted = False
    elif metric_key in policy:
        accepted = normalized_label == metric_key or _label_matches(normalized_label, policy[metric_key])
    else:
        return output
    if accepted:
        output["core_metric_eligible"] = True
        output.setdefault("semantic_status", "accepted")
        return output
    if "insurance service revenue" in normalized_label:
        reported_key = "reported_insurance_service_revenue"
    elif "financial instruments" in normalized_label and ("trading" in normalized_label or "fair value" in normalized_label):
        reported_key = "reported_trading_financial_instruments_income"
    else:
        reported_key = f"reported_{metric_key}_component"
    output["canonical_candidate"] = metric_key
    output["canonical_name"] = reported_key
    output["metric_key"] = reported_key
    output["core_metric_eligible"] = False
    output["semantic_status"] = "canonical_conflict"
    warnings = [str(item) for item in output.get("normalization_warnings") or () if item]
    warnings.append(f"financial_metric_canonical_conflict:{metric_key}:{raw_label}")
    output["normalization_warnings"] = warnings
    return output


def _label_matches(label: str, accepted_labels: tuple[str, ...]) -> bool:
    normalized = label.rstrip(" :;,.0123456789")
    return normalized in accepted_labels


def _net_profit_label_is_core(label: str, metric_key: str) -> bool:
    normalized = label.rstrip(" :;,.0123456789")
    canonical_label = metric_key.replace("_", " ")
    if normalized in {
        canonical_label,
        "net profit",
        "net income",
        "profit for the year",
        "profit for the period",
        "净利润",
        "归母净利润",
        "归属于母公司股东的净利润",
    }:
        return True
    return normalized.startswith(
        (
            "profit attributable to owners",
            "profit attributable to shareholders",
            "net profit attributable to",
            "net income attributable to",
        )
    )


def _markdown_headings(text: str, *, limit: int = 40) -> list[str]:
    headings = [line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")]
    return [item for item in headings if item][:limit]


def _selected_company_artifact(
    context: AdapterContext,
    candidates: tuple[str, ...],
    *,
    report_id: str,
) -> Path | None:
    """Use company-level fallbacks only when they are bound to this report."""

    for relative in candidates:
        candidate = company_artifact_path(context, (relative,))
        if candidate is None:
            continue
        normalized_relative = relative.replace("\\", "/")
        report_specific = f"/reports/{report_id}/" in f"/{normalized_relative}"
        if report_specific:
            return candidate
        payload = read_json(candidate)
        if _payload_matches_selected_report(payload, context=context, report_id=report_id):
            return candidate
    return None


def _payload_matches_selected_report(
    payload: Any,
    *,
    context: AdapterContext,
    report_id: str,
) -> bool:
    if not isinstance(payload, Mapping):
        return False
    identity = context.research_target.get("research_identity")
    identity = identity if isinstance(identity, Mapping) else {}
    market = str(identity.get("market") or "").upper()
    expected = {
        "report_id": report_id,
        "filing_id": str(identity.get("filing_id") or ""),
        "parse_run_id": str(identity.get("parse_run_id") or ""),
    }
    containers: list[Mapping[str, Any]] = [payload]
    for key in ("metrics", "facts", "entries", "evidence", "items", "data"):
        values = payload.get(key)
        if isinstance(values, list):
            containers.extend(item for item in values if isinstance(item, Mapping))
    observed: dict[str, set[str]] = {field: set() for field in expected}
    for container in containers:
        nested = container.get("raw") if isinstance(container.get("raw"), Mapping) else {}
        for field in expected:
            value = str(
                container.get(field)
                or nested.get(field)
                or (container.get("task_id") if field == "parse_run_id" and market == "CN" else "")
                or (nested.get("task_id") if field == "parse_run_id" and market == "CN" else "")
                or ""
            ).strip()
            if value:
                observed[field].add(value)
    required_fields = ("report_id", "parse_run_id") if market == "CN" else tuple(expected)
    for field in required_fields:
        expected_value = expected[field]
        if not expected_value or observed[field] != {expected_value}:
            return False
    if market == "CN" and observed["filing_id"] and observed["filing_id"] != {expected["filing_id"]}:
        return False
    return True


def _financial_quality_status(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    nested = payload.get("financial_checks") if isinstance(payload.get("financial_checks"), Mapping) else payload
    status = str(nested.get("overall_status") or nested.get("status") or "").strip().lower()
    if status in {"pass", "warning", "fail"}:
        return status
    summary = nested.get("summary") if isinstance(nested.get("summary"), Mapping) else {}
    if int(summary.get("fail") or 0) > 0:
        return "fail"
    if int(summary.get("warning") or 0) > 0:
        return "warning"
    return "pass" if summary else ""
