#!/usr/bin/env python3
"""Build the immutable, source-family-neutral input for formal analysis."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from input_adapters import (
    AdapterContext,
    PDFMarketAdapter,
    SecIxbrlAdapter,
    SourceAdapterError,
    source_family_for_manifest,
)
from input_adapters.base import read_json, validate_research_target

BUNDLE_SCHEMA_VERSION = "siq_analysis_input_bundle_v1"
FORBIDDEN_OUTPUT_PARTS = {"reports", "metrics", "evidence", "semantic", "graph"}


def build_analysis_input_bundle(
    *,
    research_target: Mapping[str, Any],
    company_dir: Path,
    report_dir: Path,
    manifest_path: Path | None = None,
    sec_adapter_enabled: bool | None = None,
) -> dict[str, Any]:
    company_dir = company_dir.resolve()
    report_dir = report_dir.resolve()
    try:
        report_dir.relative_to(company_dir)
    except ValueError as exc:
        raise SourceAdapterError("unsafe_path_rejected", "report directory escapes company workspace") from exc
    manifest_path = (manifest_path or report_dir / "manifest.json").resolve()
    try:
        manifest_path.relative_to(report_dir)
    except ValueError as exc:
        raise SourceAdapterError("unsafe_path_rejected", "manifest escapes selected report directory") from exc
    manifest = read_json(manifest_path, required=True)
    if not isinstance(manifest, Mapping):
        raise SourceAdapterError("source_package_not_ready", "report manifest must be a JSON object")
    target = validate_research_target(research_target, manifest)
    target_report = target.get("source_report") if isinstance(target.get("source_report"), Mapping) else {}
    declared_source_family = str(target_report.get("source_family") or "").strip().lower()
    routing_manifest = dict(manifest)
    if declared_source_family:
        routing_manifest.setdefault("source_family", declared_source_family)
    source_family = source_family_for_manifest(routing_manifest)
    if source_family == "pdf_market":
        adapter = PDFMarketAdapter()
    elif source_family == "sec_ixbrl":
        enabled = sec_adapter_enabled
        if enabled is None:
            enabled = os.getenv("SIQ_US_SEC_ANALYSIS_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
        if not enabled:
            raise SourceAdapterError("source_adapter_unavailable", "SEC analysis adapter is disabled")
        adapter = SecIxbrlAdapter()
    else:
        raise SourceAdapterError(
            "source_adapter_unavailable",
            f"source family is not supported by this release: {source_family}",
        )
    context = AdapterContext(
        research_target=target,
        company_dir=company_dir,
        report_dir=report_dir,
        manifest_path=manifest_path,
        manifest=manifest,
    )
    adapted = adapter.build(context)
    company_metadata = read_json(company_dir / "company.json") or {}
    entity_profile = _entity_profile(
        company_metadata=company_metadata if isinstance(company_metadata, Mapping) else {},
        manifest=manifest,
        facts=adapted.get("normalized_facts") or (),
    )
    capabilities = dict(adapted.get("capabilities") or {})
    capabilities.update(
        {
            "financial_institution": entity_profile["financial_institution"],
            "financial_institution_kind": entity_profile["kind"],
            "operating_cash_flow_analysis": not entity_profile["financial_institution"],
            "gross_margin_analysis": not entity_profile["financial_institution"],
            "industrial_capex_analysis": not entity_profile["financial_institution"],
        }
    )
    quality_status = str(
        adapted.get("quality_status")
        or target["source_report"].get("quality_status")
        or manifest.get("quality_status")
        or "unknown"
    )
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "research_target": target,
        "research_identity": target["research_identity"],
        "source_report": target["source_report"],
        "adapter": adapted["adapter"],
        "quality": {
            "status": quality_status,
            "warnings": _financial_warning_messages(adapted.get("financial_checks")),
            "degraded_reasons": list(adapted.get("degraded_reasons") or ()),
        },
        "capabilities": capabilities,
        "entity_profile": entity_profile,
        "normalized_facts": list(adapted.get("normalized_facts") or ()),
        "evidence_refs": list(adapted.get("evidence_refs") or ()),
        "financial_checks": dict(adapted.get("financial_checks") or {}),
        "source_inputs": dict(adapted.get("inputs") or {}),
        "source_metadata": {
            key: value
            for key, value in adapted.items()
            if key
            not in {
                "adapter",
                "capabilities",
                "degraded_reasons",
                "evidence_refs",
                "financial_checks",
                "inputs",
                "normalized_facts",
                "quality_status",
            }
        },
        "server_paths": {
            "company_dir": str(company_dir),
            "report_dir": str(report_dir),
            "manifest_path": str(manifest_path),
            "analysis_dir": str(company_dir / "analysis"),
        },
    }


def _financial_warning_messages(payload: Any) -> list[str]:
    if not isinstance(payload, Mapping):
        return []
    nested = payload.get("financial_checks") if isinstance(payload.get("financial_checks"), Mapping) else payload
    output: list[str] = []
    for key in ("warnings", "failures", "errors", "issues"):
        values = nested.get(key)
        if isinstance(values, list):
            output.extend(str(item).strip() for item in values if str(item).strip())
    summary = nested.get("summary") if isinstance(nested.get("summary"), Mapping) else {}
    if int(summary.get("warning") or 0) > 0:
        output.append(f"financial_checks:warning_count={int(summary['warning'])}")
    if int(summary.get("fail") or 0) > 0:
        output.append(f"financial_checks:fail_count={int(summary['fail'])}")
    status = str(nested.get("overall_status") or nested.get("status") or "").strip().lower()
    if status in {"warning", "fail", "failed"} and not output:
        output.append(f"financial_checks:{status}")
    return list(dict.fromkeys(output))


def _entity_profile(
    *,
    company_metadata: Mapping[str, Any],
    manifest: Mapping[str, Any],
    facts: Any,
) -> dict[str, Any]:
    metadata_text = " ".join(
        str(value or "").lower()
        for value in (
            company_metadata.get("industry"),
            company_metadata.get("sector"),
            company_metadata.get("industry_profile"),
            company_metadata.get("company_type"),
            company_metadata.get("company_short_name"),
            company_metadata.get("company_name"),
            company_metadata.get("display_name"),
            manifest.get("industry_profile"),
            manifest.get("company_name"),
        )
    )
    metric_keys = {
        str(item.get("metric_key") or item.get("canonical_name") or "").strip().lower()
        for item in facts
        if isinstance(item, Mapping)
    }
    bank_metrics = {
        "net_interest_income",
        "net_interest_margin",
        "capital_adequacy_ratio",
        "core_tier_1_capital_adequacy_ratio",
        "tier_1_capital_ratio",
        "customer_loans",
        "customer_deposits",
    }
    insurance_metrics = {
        "insurance_revenue",
        "insurance_service_result",
        "solvency_ratio",
        "combined_ratio",
        "contractual_service_margin",
    }
    kind = "general"
    reasons: list[str] = []
    if metric_keys & insurance_metrics or any(token in metadata_text for token in ("insurance", "insurer", "保险")):
        kind = "insurance"
        reasons.append("insurance_metadata_or_metrics")
    elif metric_keys & bank_metrics or any(token in metadata_text for token in ("bank", "banking", "银行")):
        kind = "bank"
        reasons.append("bank_metadata_or_metrics")
    return {
        "financial_institution": kind in {"bank", "insurance"},
        "kind": kind,
        "classification_reasons": reasons,
        "available_specialized_metrics": sorted(metric_keys & (bank_metrics | insurance_metrics)),
    }


def validate_analysis_input_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    if str(bundle.get("schema_version") or "") != BUNDLE_SCHEMA_VERSION:
        raise SourceAdapterError("source_package_not_ready", "invalid analysis input bundle schema")
    identity = bundle.get("research_identity")
    if not isinstance(identity, Mapping) or any(
        not str(identity.get(field) or "").strip() for field in ("market", "company_id", "filing_id", "parse_run_id")
    ):
        raise SourceAdapterError("research_identity_incomplete", "bundle ResearchIdentity is incomplete")
    adapter = bundle.get("adapter")
    if not isinstance(adapter, Mapping) or not str(adapter.get("source_family") or ""):
        raise SourceAdapterError("source_adapter_unavailable", "bundle adapter metadata is missing")
    return dict(bundle)


def load_analysis_input_bundle(path: Path) -> dict[str, Any]:
    payload = read_json(path, required=True)
    if not isinstance(payload, Mapping):
        raise SourceAdapterError("source_package_not_ready", "analysis input bundle must be a JSON object")
    return validate_analysis_input_bundle(payload)


def write_analysis_input_bundle(path: Path, bundle: Mapping[str, Any]) -> None:
    validate_analysis_input_bundle(bundle)
    resolved = path.resolve()
    if any(part in FORBIDDEN_OUTPUT_PARTS for part in resolved.parts):
        raise SourceAdapterError(
            "unsafe_path_rejected", "input bundle cannot be written into immutable source directories"
        )
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_suffix(f"{resolved.suffix}.tmp")
    temporary.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(resolved)
