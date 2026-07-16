"""Market-neutral renderer for a resolved formal analysis input bundle."""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from analysis_input_bundle import validate_analysis_input_bundle
from analysis_market_policy import (
    CHAPTER_IDS as MARKET_POLICY_CHAPTER_IDS,
    POLICY_SCHEMA_VERSION,
    build_analysis_market_policy,
)
from input_adapters import SourceAdapterError

REPORT_SCHEMA_VERSION = "siq_analysis_report_v2"
ARTIFACT_SCHEMA_VERSION = "siq_agent_artifact_v2"
MAX_PRESENTATION_EVIDENCE_ITEMS = 64
MAX_PRESENTATION_HTML_BYTES = 512 * 1024

CLAIM_METRIC_KEYS = {
    "revenue",
    "operating_revenue",
    "total_revenue",
    "net_income",
    "net_profit",
    "parent_net_profit",
    "net_profit_parent",
    "operating_profit",
    "operating_income",
    "operating_cash_flow",
    "operating_cash_flow_net",
    "net_operating_cash_flow",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "gross_margin",
    "eps",
    "weighted_avg_roe",
    "net_interest_income",
    "net_interest_margin",
    "capital_adequacy_ratio",
    "core_tier_1_capital_adequacy_ratio",
    "tier_1_capital_ratio",
    "insurance_revenue",
    "insurance_service_result",
    "solvency_ratio",
}

METRIC_LABELS = {
    "revenue": "营业收入",
    "operating_revenue": "营业收入",
    "total_revenue": "总收入",
    "net_income": "净利润",
    "net_profit": "净利润",
    "parent_net_profit": "归母净利润",
    "net_profit_parent": "归母净利润",
    "operating_income": "营业利润",
    "operating_profit": "营业利润",
    "operating_cash_flow": "经营现金流",
    "operating_cash_flow_net": "经营现金流净额",
    "net_operating_cash_flow": "经营现金流净额",
    "total_assets": "总资产",
    "total_liabilities": "总负债",
    "total_equity": "股东权益",
    "gross_margin": "毛利率",
    "eps": "每股收益",
    "weighted_avg_roe": "加权平均净资产收益率",
    "net_interest_income": "净利息收入",
    "net_interest_margin": "净息差",
    "capital_adequacy_ratio": "资本充足率",
    "core_tier_1_capital_adequacy_ratio": "核心一级资本充足率",
    "insurance_revenue": "保险服务收入",
    "insurance_service_result": "保险服务结果",
    "solvency_ratio": "偿付能力充足率",
}

SECTION_SPECS = (
    ("executive_summary", "执行摘要"),
    ("business_overview", "业务与披露口径"),
    ("revenue_quality", "收入与增长质量"),
    ("profitability", "盈利能力"),
    ("balance_sheet", "资产负债结构"),
    ("cash_flow", "现金流质量"),
    ("capital_allocation", "资本配置"),
    ("segments", "分部与经营驱动"),
    ("risk_factors", "风险因素"),
    ("controls", "内部控制与治理"),
    ("accounting_quality", "会计口径与指标质量"),
    ("valuation_boundary", "估值数据边界"),
    ("tracking", "后续跟踪清单"),
    ("traceability", "数据质量与溯源"),
)


def render_analysis_bundle(
    bundle: Mapping[str, Any],
    *,
    output_prefix: Path,
    research_pack_result: Mapping[str, Any],
    staging_dir: Path,
    allow_overwrite: bool = False,
) -> dict[str, Any]:
    payload = validate_analysis_input_bundle(bundle)
    pack_validation = (
        research_pack_result.get("validation") if isinstance(research_pack_result.get("validation"), Mapping) else {}
    )
    packs = research_pack_result.get("packs") if isinstance(research_pack_result.get("packs"), list) else []
    if pack_validation.get("ok") is not True or len(packs) < 5:
        raise SourceAdapterError(
            "research_pack_validation_failed",
            "formal analysis requires validated shared research packs",
        )
    output_prefix = output_prefix.resolve()
    analysis_dir = Path(str((payload.get("server_paths") or {}).get("analysis_dir") or "")).resolve()
    try:
        output_prefix.parent.relative_to(analysis_dir)
    except ValueError as exc:
        raise SourceAdapterError("unsafe_path_rejected", "formal analysis output must stay inside analysis/") from exc
    resolved_staging_dir = staging_dir.resolve()
    try:
        staging_relative = resolved_staging_dir.relative_to(analysis_dir)
    except ValueError as exc:
        raise SourceAdapterError("unsafe_path_rejected", "publication staging escapes analysis/") from exc
    if not staging_relative.parts or staging_relative.parts[0] != ".work":
        raise SourceAdapterError("unsafe_path_rejected", "publication staging must stay inside analysis/.work/")
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    resolved_staging_dir.mkdir(parents=True, exist_ok=True)
    report = _build_report(payload, research_pack_result=research_pack_result)
    markdown = _render_markdown(report)
    html_text = _render_html(report)
    report_json = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    content_hashes = {
        "md": _sha256(markdown.encode("utf-8")),
        "json": _sha256(report_json.encode("utf-8")),
        "html": _sha256(html_text.encode("utf-8")),
    }
    artifact_id = _artifact_id(report, content_hashes["html"])
    artifact_prefix = output_prefix.parent / artifact_id
    paths = {
        "md": artifact_prefix.with_suffix(".md"),
        "json": artifact_prefix.with_suffix(".json"),
        "html": artifact_prefix.with_suffix(".html"),
        "sidecar": output_prefix.parent / f"{artifact_id}.artifact.json",
    }
    staged_paths = {key: resolved_staging_dir / path.name for key, path in paths.items()}
    existing = [path for path in paths.values() if path.exists()]
    if existing and not allow_overwrite:
        raise SourceAdapterError("artifact_exists", "formal analysis artifact already exists")
    quality = report["quality"]
    warning_messages = [str(item) for item in quality.get("warnings") or ()]
    warning_messages.extend(str(item) for item in quality.get("degraded_reasons") or ())
    policy_quality = (
        report.get("market_policy", {}).get("quality", {}) if isinstance(report.get("market_policy"), Mapping) else {}
    )
    warning_messages.extend(str(item) for item in policy_quality.get("warning_summary") or ())
    artifact_status = (
        "degraded"
        if quality.get("status") != "pass" or policy_quality.get("status") != "ready" or warning_messages
        else "completed"
    )
    unresolved_count = sum(1 for item in report["evidence_refs"] if not _evidence_has_locator(item))
    sidecar = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "artifact_id": artifact_id,
        "artifact_type": "analysis",
        "status": artifact_status,
        "created_at": report["generated_at"],
        "research_target": report["research_target"],
        "source_report_id": report["source_report"]["report_id"],
        "source_family": report["adapter"]["source_family"],
        "adapter_version": report["adapter"]["version"],
        "upstream_artifact_ids": [],
        "html_file": paths["html"].name,
        "content_hash": content_hashes["html"],
        "quality": {
            "status": report["quality"].get("status") or "unknown",
            "warnings": list(dict.fromkeys(warning_messages)),
        },
        "evidence_summary": {
            "citation_count": len(report["evidence_refs"]),
            "unresolved_count": unresolved_count,
        },
        "metadata": {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "markdown_file": paths["md"].name,
            "json_file": paths["json"].name,
            "content_hashes": content_hashes,
            "quality_status_preserved": report["quality"].get("status") or "unknown",
            "research_pack_schema_version": str(
                (research_pack_result.get("manifest") or {}).get("schema_version") or ""
            ),
            "research_pack_validation_status": pack_validation.get("status"),
            "research_pack_merge_schema_version": str(
                (research_pack_result.get("merge_manifest") or {}).get("schema_version") or ""
            ),
            "claims": report["claims"],
            "evidence_catalog": {
                "rendered_count": len(_presentation_evidence(report)),
                "total_count": len(report["evidence_refs"]),
                "limit": MAX_PRESENTATION_EVIDENCE_ITEMS,
                "full_evidence_file": paths["json"].name,
            },
            "market_policy": {
                "schema_version": str(report.get("market_policy", {}).get("schema_version") or ""),
                "market": dict(report.get("market_policy", {}).get("market") or {}),
                "quality_status": str(policy_quality.get("status") or "unknown"),
                "warning_codes": [
                    str(item.get("code") or "")
                    for item in policy_quality.get("warnings") or ()
                    if isinstance(item, Mapping) and item.get("code")
                ],
            },
        },
    }
    _validate_shared_contracts(report, sidecar)
    validation = _validate_rendered_report(report, markdown, html_text, sidecar)
    if not validation["ok"]:
        raise SourceAdapterError(
            "validation_failed",
            "formal analysis failed its in-memory publication gate",
            details={"failures": validation["failures"]},
        )
    staged_content = {
        "md": markdown,
        "json": report_json,
        "html": html_text,
        "sidecar": json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n",
    }
    for key, content in staged_content.items():
        _atomic_write(staged_paths[key], content)
    publication_validation = _validate_staged_publication(
        staged_paths,
        expected_hashes=content_hashes,
        sidecar=sidecar,
    )
    if not publication_validation["ok"]:
        raise SourceAdapterError(
            "publication_staging_invalid",
            "staged formal analysis artifacts failed hash validation",
            details={"failures": publication_validation["failures"]},
        )
    _publish_staged_files(
        staged_paths,
        paths,
        allow_overwrite=allow_overwrite,
    )
    return {
        "ok": validation["ok"],
        "stage": "completed" if validation["ok"] else "validation_failed",
        "files": {key: str(path) for key, path in paths.items()},
        "validation": validation,
        "artifact_id": artifact_id,
        "research_identity": report["research_identity"],
        "source_report": report["source_report"],
        "adapter": report["adapter"],
        "quality": report["quality"],
        "checkpoints": {
            "research_pack_manifest": str(
                (research_pack_result.get("paths") or {}).get("research_pack_manifest") or ""
            ),
            "research_pack_validation": str(
                (research_pack_result.get("paths") or {}).get("research_pack_validation") or ""
            ),
            "publication_staging_validation": publication_validation,
        },
    }


def _build_report(
    bundle: Mapping[str, Any],
    *,
    research_pack_result: Mapping[str, Any],
) -> dict[str, Any]:
    target = dict(bundle["research_target"])
    facts = [dict(item) for item in bundle.get("normalized_facts", ()) if isinstance(item, Mapping)]
    evidence = [dict(item) for item in bundle.get("evidence_refs", ()) if isinstance(item, Mapping)]
    facts = sorted(facts, key=_fact_sort_key)
    analysis_facts = [fact for fact in facts if _fact_is_analysis_eligible(fact)]
    fact_by_key: dict[str, list[dict[str, Any]]] = {}
    for fact in analysis_facts:
        fact_by_key.setdefault(str(fact.get("metric_key") or "unknown"), []).append(fact)
    entity_profile = dict(bundle.get("entity_profile") or {})
    source_metadata = dict(bundle.get("source_metadata") or {})
    market_policy = build_analysis_market_policy(
        str((bundle.get("research_identity") or {}).get("market") or ""),
        source_report=bundle.get("source_report") if isinstance(bundle.get("source_report"), Mapping) else {},
        source_metadata=source_metadata,
        financial_checks=bundle.get("financial_checks") if isinstance(bundle.get("financial_checks"), Mapping) else {},
        entity_profile=entity_profile,
    )
    claims = _build_claims(analysis_facts, evidence)
    merge_manifest = (
        research_pack_result.get("merge_manifest")
        if isinstance(research_pack_result.get("merge_manifest"), Mapping)
        else {}
    )
    merge_sections = merge_manifest.get("sections") if isinstance(merge_manifest.get("sections"), Mapping) else {}
    known_evidence_ids = {_evidence_id(item) for item in evidence}
    sections = []
    for section_id, title in SECTION_SPECS:
        pack_refs = dict(merge_sections.get(section_id) or {})
        merged_evidence_ids = [
            str(item) for item in pack_refs.get("evidence_ids") or () if str(item) in known_evidence_ids
        ]
        section_evidence_ids = list(
            dict.fromkeys([*_section_evidence_ids(section_id, fact_by_key, evidence), *merged_evidence_ids])
        )
        content = _section_content(section_id, bundle, fact_by_key, evidence)
        if section_id == "executive_summary":
            context = market_policy.get("reporting_context") or {}
            content.extend(
                item
                for item in (
                    f"本报告采用{context.get('policy_context')}，市场政策只用于选择分析口径，不替代公司事实证据。"
                    if context.get("policy_context")
                    else "",
                    str(context.get("boundary_note") or ""),
                )
                if item
            )
        policy_sections = market_policy.get("sections") if isinstance(market_policy.get("sections"), Mapping) else {}
        policy_insights = policy_sections.get(section_id) if isinstance(policy_sections.get(section_id), list) else []
        content.extend(
            str(item.get("text") or "")
            for item in policy_insights
            if isinstance(item, Mapping) and str(item.get("text") or "").strip()
        )
        sections.append(
            {
                "section_id": section_id,
                "title": title,
                "status": _section_status(section_id, bundle, fact_by_key, evidence),
                "content": list(dict.fromkeys(content)),
                "evidence_ids": section_evidence_ids[:4],
                "research_pack_refs": {
                    key: list(dict.fromkeys(str(item) for item in pack_refs.get(key) or () if item))
                    for key in ("agent_ids", "finding_ids", "fact_ids", "evidence_ids")
                },
            }
        )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "title": f"{target.get('display_name') or target.get('display_code') or '公司'}财务诊断报告",
        "company": {
            "company_key": target.get("company_key"),
            "company_wiki_id": target.get("company_wiki_id"),
            "display_code": target.get("display_code"),
            "display_name": target.get("display_name"),
        },
        "research_target": target,
        "research_identity": dict(bundle["research_identity"]),
        "source_report": dict(bundle["source_report"]),
        "adapter": dict(bundle["adapter"]),
        "quality": dict(bundle.get("quality") or {}),
        "capabilities": dict(bundle.get("capabilities") or {}),
        "entity_profile": entity_profile,
        "market_policy": market_policy,
        "kpis": _build_kpis(fact_by_key, entity_profile),
        "visuals": _build_visuals(fact_by_key, entity_profile),
        "claims": claims,
        "research_pack": {
            "schema_version": str((research_pack_result.get("manifest") or {}).get("schema_version") or ""),
            "validation_status": str((research_pack_result.get("validation") or {}).get("status") or ""),
            "merge_schema_version": str(merge_manifest.get("schema_version") or ""),
            "agent_ids": [
                str(item.get("agent_id") or "")
                for item in research_pack_result.get("packs") or ()
                if isinstance(item, Mapping)
            ],
        },
        "facts": facts,
        "analysis_fact_count": len(analysis_facts),
        "excluded_fact_count": len(facts) - len(analysis_facts),
        "evidence_refs": evidence,
        "sections": sections,
        "disclaimer": "本报告仅基于所选上市公司公开披露资料，不构成投资建议。",
    }


def _fact_sort_key(fact: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(fact.get("statement_type") or ""),
        str(fact.get("metric_key") or ""),
        str(fact.get("period_end") or fact.get("period") or ""),
    )


def _fact_is_analysis_eligible(fact: Mapping[str, Any]) -> bool:
    return (
        fact.get("core_metric_eligible") is not False
        and str(fact.get("semantic_status") or "").lower() != "canonical_conflict"
    )


def _build_claims(
    facts: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    evidence_by_id = {_evidence_id(item): item for item in evidence}
    by_key: dict[str, list[Mapping[str, Any]]] = {}
    for fact in facts:
        metric_key = str(fact.get("metric_key") or "")
        if (
            metric_key not in CLAIM_METRIC_KEYS
            or not _fact_is_analysis_eligible(fact)
            or _numeric_value(fact) is None
            or not _fact_evidence_ids(fact)
        ):
            continue
        by_key.setdefault(metric_key, []).append(fact)
    claims: list[dict[str, Any]] = []
    for metric_key, metric_facts in sorted(by_key.items()):
        ordered = sorted(metric_facts, key=lambda item: str(item.get("period_end") or item.get("period") or ""))
        current = ordered[-1]
        current_value = _numeric_value(current)
        period = str(current.get("period_end") or current.get("period") or "")
        label = METRIC_LABELS.get(metric_key, f"原披露指标：{current.get('raw_label') or metric_key}")
        evidence_ids = _fact_evidence_ids(current)
        refs = _claim_evidence_refs(current, evidence_by_id)
        display_value = _format_fact_value(current)
        claims.append(
            {
                "claim_id": stable_claim_id("metric_value", metric_key, period, current_value),
                "claim": f"{label}在{period or '本报告期'}为{display_value}。",
                "claim_type": "metric_value",
                "metric_key": metric_key,
                "period": period,
                "normalized_value": current_value,
                "display_value": display_value,
                "unit": str(current.get("unit") or ""),
                "currency": str(current.get("currency") or "") or None,
                "evidence_ids": evidence_ids,
                "evidence_refs": refs,
            }
        )
        for previous in reversed(ordered[:-1]):
            if not _facts_comparable(current, previous):
                continue
            previous_value = _numeric_value(previous)
            if current_value is None or previous_value in (None, 0):
                continue
            change_pct = (current_value - previous_value) / abs(previous_value) * 100
            comparison_period = str(previous.get("period_end") or previous.get("period") or "")
            previous_ids = _fact_evidence_ids(previous)
            trend_ids = list(dict.fromkeys([*evidence_ids, *previous_ids]))
            trend_refs = _unique_evidence_refs([*refs, *_claim_evidence_refs(previous, evidence_by_id)])
            direction = "增长" if change_pct >= 0 else "下降"
            claims.append(
                {
                    "claim_id": stable_claim_id(
                        "metric_change", metric_key, comparison_period, period, round(change_pct, 8)
                    ),
                    "claim": f"{label}由{comparison_period}至{period}{direction}{abs(change_pct):.2f}%。",
                    "claim_type": "metric_change",
                    "metric_key": metric_key,
                    "period": period,
                    "normalized_value": current_value,
                    "display_value": display_value,
                    "unit": str(current.get("unit") or ""),
                    "currency": str(current.get("currency") or "") or None,
                    "comparison_period": comparison_period,
                    "comparison_value": previous_value,
                    "change_pct": round(change_pct, 8),
                    "evidence_ids": trend_ids,
                    "evidence_refs": trend_refs,
                }
            )
            break
    if claims:
        return claims
    fallback = next((item for item in evidence if _evidence_has_locator(item)), None)
    if fallback is None:
        return []
    evidence_id = _evidence_id(fallback)
    label = _evidence_semantic_label(fallback)
    return [
        {
            "claim_id": stable_claim_id("disclosure", evidence_id),
            "claim": f"当前报告包含“{label}”对应的可回溯披露；因缺少可核验核心指标，不进一步扩展数值结论。",
            "claim_type": "disclosure",
            "evidence_ids": [evidence_id],
            "evidence_refs": [dict(fallback)],
        }
    ]


def stable_claim_id(*parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return "claim_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _unique_fact_evidence_refs(fact: Mapping[str, Any]) -> list[dict[str, Any]]:
    refs = fact.get("evidence_refs") if isinstance(fact.get("evidence_refs"), list) else []
    return _unique_evidence_refs(item for item in refs if isinstance(item, Mapping))


def _claim_evidence_refs(
    fact: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    refs = _unique_fact_evidence_refs(fact)
    known = {_evidence_id(item) for item in refs}
    for evidence_id in _fact_evidence_ids(fact):
        evidence = evidence_by_id.get(evidence_id)
        if evidence is not None and evidence_id not in known:
            refs.append(dict(evidence))
            known.add(evidence_id)
    return refs


def _unique_evidence_refs(refs: Sequence[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        evidence_id = _evidence_id(ref)
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        output.append(dict(ref))
    return output


def _latest(fact_by_key: Mapping[str, Sequence[Mapping[str, Any]]], *keys: str) -> Mapping[str, Any] | None:
    candidates = [item for key in keys for item in fact_by_key.get(key, ())]
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: str(item.get("period_end") or item.get("period") or ""))[-1]


def _section_status(
    section_id: str,
    bundle: Mapping[str, Any],
    fact_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
    evidence: Sequence[Mapping[str, Any]],
) -> str:
    capabilities = bundle.get("capabilities") if isinstance(bundle.get("capabilities"), Mapping) else {}
    financial_institution = bool(capabilities.get("financial_institution"))
    if financial_institution and section_id == "cash_flow":
        return "not_applicable"
    if financial_institution and section_id == "capital_allocation":
        return "ready" if capabilities.get("structured_metrics") else "degraded"
    if section_id == "valuation_boundary" and not capabilities.get("market_snapshot"):
        return "unavailable"
    if section_id == "segments" and not any("segment" in key for key in fact_by_key):
        return "degraded"
    role_requirements = {
        "business_overview": {"business", "mda"},
        "risk_factors": {"risk_factors", "market_risk", "mda"},
        "controls": {"controls"},
    }
    if section_id in role_requirements and not any(
        str(item.get("section_role") or "") in role_requirements[section_id] for item in evidence
    ):
        return "degraded"
    if section_id == "tracking" and not any(fact_by_key.values()):
        return "degraded"
    if section_id == "traceability" and not evidence:
        return "degraded"
    return "ready"


def _section_content(
    section_id: str,
    bundle: Mapping[str, Any],
    fact_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
    evidence: Sequence[Mapping[str, Any]],
) -> list[str]:
    source_report = bundle.get("source_report") if isinstance(bundle.get("source_report"), Mapping) else {}
    quality = bundle.get("quality") if isinstance(bundle.get("quality"), Mapping) else {}
    source_metadata = bundle.get("source_metadata") if isinstance(bundle.get("source_metadata"), Mapping) else {}
    entity_profile = bundle.get("entity_profile") if isinstance(bundle.get("entity_profile"), Mapping) else {}
    institution_kind = str(entity_profile.get("kind") or "general")
    financial_institution = bool(entity_profile.get("financial_institution"))
    section_catalog = (
        source_metadata.get("section_catalog") if isinstance(source_metadata.get("section_catalog"), list) else []
    )
    facts = {
        "revenue": _latest(fact_by_key, "revenue", "operating_revenue", "total_revenue"),
        "profit": _latest(fact_by_key, "net_income", "net_profit", "parent_net_profit", "net_profit_parent"),
        "ocf": _latest(fact_by_key, "operating_cash_flow", "operating_cash_flow_net", "net_operating_cash_flow"),
        "assets": _latest(fact_by_key, "total_assets"),
        "liabilities": _latest(fact_by_key, "total_liabilities"),
        "net_interest_income": _latest(fact_by_key, "net_interest_income"),
        "capital_adequacy": _latest(
            fact_by_key,
            "capital_adequacy_ratio",
            "core_tier_1_capital_adequacy_ratio",
            "tier_1_capital_ratio",
        ),
        "insurance_revenue": _latest(fact_by_key, "insurance_revenue"),
        "solvency": _latest(fact_by_key, "solvency_ratio"),
    }
    if section_id == "executive_summary":
        summary_metrics = (
            (("net_interest_income", "净利息收入"), ("profit", "利润"), ("assets", "总资产"))
            if institution_kind == "bank"
            else (("insurance_revenue", "保险服务收入"), ("profit", "利润"), ("assets", "总资产"))
            if institution_kind == "insurance"
            else (("revenue", "收入"), ("profit", "利润"), ("ocf", "经营现金流"))
        )
        available = [f"{label}{_format_fact(facts[key])}" for key, label in summary_metrics if facts[key]]
        change_candidates = (
            (
                _change_sentence("净利息收入", fact_by_key, "net_interest_income"),
                _change_sentence(
                    "利润", fact_by_key, "net_income", "net_profit", "parent_net_profit", "net_profit_parent"
                ),
            )
            if institution_kind == "bank"
            else (
                _change_sentence("保险服务收入", fact_by_key, "insurance_revenue"),
                _change_sentence(
                    "利润", fact_by_key, "net_income", "net_profit", "parent_net_profit", "net_profit_parent"
                ),
            )
            if institution_kind == "insurance"
            else (
                _change_sentence("收入", fact_by_key, "revenue", "operating_revenue", "total_revenue"),
                _change_sentence(
                    "利润", fact_by_key, "net_income", "net_profit", "parent_net_profit", "net_profit_parent"
                ),
                _change_sentence(
                    "经营现金流",
                    fact_by_key,
                    "operating_cash_flow",
                    "operating_cash_flow_net",
                    "net_operating_cash_flow",
                ),
            )
        )
        changes = [item for item in change_candidates if item]
        quality_status = str(quality.get("status") or "unknown")
        quality_sentence = (
            "源数据质量状态为 warning，报告和 sidecar 保留降级状态，未静默提升为 pass。"
            if quality_status == "warning"
            else f"源数据质量状态为 {quality_status}；财务检查告警仍按 sidecar 单独保留。"
        )
        quality_issue_count = len(quality.get("warnings") or ()) + len(quality.get("degraded_reasons") or ())
        content = [
            f"报告严格绑定 {source_report.get('report_id')}，期间截止日为 {source_report.get('period_end') or '未披露'}。",
            "；".join(available) if available else "结构化核心财务指标暂不可用，结论保持降级。",
            "；".join(item.rstrip("。") for item in changes) + "。"
            if changes
            else "当前包没有满足同币种、同口径条件的可比期间，不计算同比。",
            quality_sentence,
        ]
        if quality_issue_count:
            content.append(
                f"当前输入共保留 {quality_issue_count} 项质量告警或降级原因；技术详情保留在结构化质量字段中，不直接作为公司经营结论。"
            )
        return content
    if section_id == "business_overview":
        content = [
            f"披露类型：{source_report.get('form_type') or source_report.get('report_type') or '未披露'}；会计准则：{source_report.get('accounting_standard') or '未披露'}。",
            "业务判断以所选报告全文和可回溯章节为边界，不以其他公司或其他报告补齐。",
        ]
        disclosure = _section_disclosure(section_catalog, "business")
        return content + ([f"本期业务披露摘要：{disclosure}"] if disclosure else [])
    if section_id == "revenue_quality":
        if institution_kind == "bank":
            return [
                _fact_sentence("净利息收入", facts["net_interest_income"]),
                "银行收入质量按净利息收入、净息差和手续费等金融机构口径观察，不套用工业企业毛利率。",
            ]
        if institution_kind == "insurance":
            return [
                _fact_sentence("保险服务收入", facts["insurance_revenue"]),
                "保险业务按保险服务收入、保险服务结果和偿付能力口径观察，不套用工业企业毛利率。",
            ]
        return [
            _fact_sentence("收入", facts["revenue"]),
            _change_sentence("收入", fact_by_key, "revenue", "operating_revenue", "total_revenue")
            or "缺少可比期间或口径不一致，不计算收入同比。",
        ]
    if section_id == "profitability":
        content = [
            _fact_sentence("利润", facts["profit"]),
            _change_sentence("利润", fact_by_key, "net_income", "net_profit", "parent_net_profit", "net_profit_parent")
            or "缺少可比期间或口径不一致，不计算利润同比。",
            "GAAP 与公司自定义指标按 accounting_basis 区分，不混合作为同一口径。",
        ]
        disclosure = _section_disclosure(section_catalog, "mda")
        return content + ([f"管理层讨论摘要：{disclosure}"] if disclosure else [])
    if section_id == "balance_sheet":
        return [
            _fact_sentence("总资产", facts["assets"]),
            _fact_sentence("总负债", facts["liabilities"]),
            _ratio_sentence("资产负债率", facts["liabilities"], facts["assets"])
            or "资产与负债期间、币种或口径不可比，不计算资产负债率。",
        ]
    if section_id == "cash_flow":
        if financial_institution:
            return [
                "本章对金融机构标记为不适用：经营现金流受存贷款、保险负债及监管资产负债管理影响，不按一般工业公司现金利润匹配度评价。"
            ]
        return [
            _fact_sentence("经营现金流", facts["ocf"]),
            _ratio_sentence("经营现金流/净利润", facts["ocf"], facts["profit"])
            or "经营现金流与利润期间、币种或口径不可比，不计算现金利润匹配度。",
            "现金流指标缺失时不以零值替代。",
        ]
    if section_id == "capital_allocation":
        if institution_kind == "bank":
            return [
                _fact_sentence("资本充足率", facts["capital_adequacy"]),
                "资本配置以监管资本、风险加权资产和分红约束为核心，不套用工业资本开支模板。",
            ]
        if institution_kind == "insurance":
            return [
                _fact_sentence("偿付能力充足率", facts["solvency"]),
                "资本配置以偿付能力、保险负债和投资资产匹配为核心，不套用工业资本开支模板。",
            ]
        return ["资本开支、分红和回购仅在当前报告存在结构化事实时采用；当前缺口明确保留。"]
    if section_id == "segments":
        disclosure = _section_disclosure(section_catalog, "segments")
        return [
            f"分部披露摘要：{disclosure}"
            if disclosure
            else "分部结论仅引用当前报告的 segment 事实或章节；未解析时标记为降级，不套用其他市场模板。"
        ]
    if section_id == "risk_factors":
        disclosure = _section_disclosure(section_catalog, "risk_factors")
        return [
            "风险判断优先使用报告风险章节、MD&A 和财务检查告警；不以行情波动替代公司披露。",
            f"本期风险披露摘要：{disclosure}"
            if disclosure
            else "当前报告包未提供独立 Risk Factors 章节摘要，风险结论保持降级。",
        ]
    if section_id == "controls":
        disclosure = _section_disclosure(section_catalog, "controls")
        return [
            f"内部控制披露摘要：{disclosure}"
            if disclosure
            else "内部控制与治理信息仅在报告存在相应章节或证据定位时陈述。"
        ]
    if section_id == "accounting_quality":
        bases = sorted(
            {
                str(item.get("accounting_basis"))
                for items in fact_by_key.values()
                for item in items
                if item.get("accounting_basis")
            }
        )
        return [
            f"识别到的指标基础：{', '.join(bases) if bases else '未标注'}。",
            "同名 XBRL concept 按 context、期间、QTD/YTD 和 dimensions 保持可区分；公司扩展概念不自动等同 non-GAAP。",
        ]
    if section_id == "valuation_boundary":
        return ["本报告未接入可比的实时行情与估值快照，因此不输出估值倍数或目标价。"]
    if section_id == "tracking":
        return ["后续应跟踪收入、利润、经营现金流、资产负债变化及报告中已披露的主要风险。"]
    unresolved = sum(not _evidence_has_locator(item) for item in evidence)
    all_facts = [item for item in bundle.get("normalized_facts") or () if isinstance(item, Mapping)]
    excluded = sum(not _fact_is_analysis_eligible(item) for item in all_facts)
    return [
        f"当前报告保留 {len(evidence)} 条证据定位，其中 {len(evidence) - unresolved} 条具备可回溯定位；全部证据均绑定当前 ResearchIdentity。",
        f"共读取 {len(all_facts)} 条结构化事实，{excluded} 条因核心指标资格或语义冲突仅保留在审计数据中，未进入摘要、图表或数值声明。",
        "源报告、指标与证据保持只读；完整证据数组保存在 JSON 结构化附件中，HTML 仅折叠展示核心结论实际引用的可读定位。",
    ]


def _section_evidence_ids(
    section_id: str,
    fact_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
    evidence: Sequence[Mapping[str, Any]],
) -> list[str]:
    roles_by_section = {
        "business_overview": {"business"},
        "revenue_quality": {"mda"},
        "profitability": {"mda"},
        "segments": {"segments", "notes"},
        "risk_factors": {"risk_factors", "market_risk"},
        "controls": {"controls"},
        "accounting_quality": {"notes", "financial_statements"},
    }
    if section_id in roles_by_section:
        matched = [item for item in evidence if str(item.get("section_role") or "") in roles_by_section[section_id]]
        return [_evidence_id(item) for item in matched[:12]]
    if section_id == "traceability":
        return []
    ids: list[str] = []
    for items in fact_by_key.values():
        for fact in items:
            refs = fact.get("evidence_refs") if isinstance(fact.get("evidence_refs"), list) else []
            ids.extend(_evidence_id(item) for item in refs if isinstance(item, Mapping))
            ids.extend(str(item) for item in fact.get("evidence_ids", ()) if item)
    return list(dict.fromkeys(ids))[:12]


def _fact_sentence(label: str, fact: Mapping[str, Any] | None) -> str:
    return f"{label}：{_format_fact(fact)}。" if fact else f"{label}：数据不可用。"


def _change_sentence(
    label: str,
    fact_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
    *keys: str,
) -> str:
    candidates = [item for key in keys for item in fact_by_key.get(key, ())]
    comparable = [item for item in candidates if _numeric_value(item) is not None]
    comparable.sort(key=lambda item: str(item.get("period_end") or item.get("period") or ""))
    for current_index in range(len(comparable) - 1, 0, -1):
        current = comparable[current_index]
        for previous in reversed(comparable[:current_index]):
            if not _facts_comparable(current, previous):
                continue
            current_value = _numeric_value(current)
            previous_value = _numeric_value(previous)
            if current_value is None or previous_value in (None, 0):
                continue
            change = (current_value - previous_value) / abs(previous_value) * 100
            direction = "增长" if change >= 0 else "下降"
            return (
                f"{label}较上一可比期间{direction}{abs(change):.2f}%"
                f"（{previous.get('period_end')} -> {current.get('period_end')}）。"
            )
    return ""


def _ratio_sentence(label: str, numerator: Mapping[str, Any] | None, denominator: Mapping[str, Any] | None) -> str:
    if not numerator or not denominator or not _facts_comparable(numerator, denominator, require_distinct_period=False):
        return ""
    top = _numeric_value(numerator)
    bottom = _numeric_value(denominator)
    if top is None or bottom in (None, 0):
        return ""
    return f"{label}为 {top / bottom * 100:.2f}%（同期间、同币种结构化指标计算）。"


def _numeric_value(fact: Mapping[str, Any]) -> float | None:
    value = fact.get("normalized_value") if fact.get("normalized_value") is not None else fact.get("value")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _facts_comparable(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    require_distinct_period: bool = True,
) -> bool:
    left_period = str(left.get("period_end") or left.get("period") or "")
    right_period = str(right.get("period_end") or right.get("period") or "")
    if not left_period or not right_period:
        return False
    if require_distinct_period and left_period == right_period:
        return False
    if not require_distinct_period and left_period != right_period:
        return False
    for field in (
        "currency",
        "accounting_standard",
        "accounting_basis",
        "scope",
        "qtd_ytd_type",
        "period_basis",
    ):
        left_value = str(left.get(field) or "").strip().upper()
        right_value = str(right.get(field) or "").strip().upper()
        if left_value != right_value:
            return False
    left_dimensions = left.get("dimensions") if isinstance(left.get("dimensions"), Mapping) else {}
    right_dimensions = right.get("dimensions") if isinstance(right.get("dimensions"), Mapping) else {}
    if dict(left_dimensions) != dict(right_dimensions):
        return False
    return True


def _build_kpis(
    fact_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
    entity_profile: Mapping[str, Any],
) -> list[dict[str, Any]]:
    kind = str(entity_profile.get("kind") or "general")
    specs = (
        (
            ("net_interest_income",),
            ("parent_net_profit", "net_profit", "net_income"),
            ("total_assets",),
            ("capital_adequacy_ratio", "core_tier_1_capital_adequacy_ratio"),
        )
        if kind == "bank"
        else (
            ("insurance_revenue",),
            ("parent_net_profit", "net_profit", "net_income"),
            ("total_assets",),
            ("solvency_ratio",),
        )
        if kind == "insurance"
        else (
            ("revenue", "operating_revenue", "total_revenue"),
            ("parent_net_profit", "net_profit", "net_income"),
            ("operating_cash_flow", "operating_cash_flow_net", "net_operating_cash_flow"),
            ("total_assets",),
        )
    )
    kpis: list[dict[str, Any]] = []
    for keys in specs:
        fact = _latest(fact_by_key, *keys)
        if not fact or _numeric_value(fact) is None:
            continue
        metric_key = str(fact.get("metric_key") or keys[0])
        kpis.append(
            {
                "metric_key": metric_key,
                "label": METRIC_LABELS.get(metric_key, str(fact.get("raw_label") or metric_key)),
                "value": _format_fact(fact),
                "fact_id": str(fact.get("fact_id") or ""),
                "evidence_ids": _fact_evidence_ids(fact),
            }
        )
    return kpis


def _chart_signature(fact: Mapping[str, Any]) -> tuple[str, ...]:
    dimensions = fact.get("dimensions") if isinstance(fact.get("dimensions"), Mapping) else {}
    return (
        str(fact.get("currency") or "").upper(),
        str(fact.get("accounting_standard") or "").upper(),
        str(fact.get("accounting_basis") or "").upper(),
        str(fact.get("scope") or "").upper(),
        str(fact.get("qtd_ytd_type") or "").upper(),
        json.dumps(dimensions, ensure_ascii=False, sort_keys=True),
    )


def _trend_visual(
    fact_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
    entity_profile: Mapping[str, Any],
) -> dict[str, Any] | None:
    kind = str(entity_profile.get("kind") or "general")
    keys = (
        ("net_interest_income", "parent_net_profit", "operating_profit", "total_assets", "operating_revenue")
        if kind == "bank"
        else ("insurance_revenue", "parent_net_profit", "operating_profit", "total_assets", "operating_revenue")
        if kind == "insurance"
        else (
            "revenue",
            "operating_revenue",
            "total_revenue",
            "parent_net_profit",
            "net_profit",
            "operating_cash_flow_net",
            "total_assets",
        )
    )
    best: tuple[str, list[Mapping[str, Any]]] | None = None
    for key in keys:
        grouped: dict[tuple[str, ...], dict[str, list[Mapping[str, Any]]]] = {}
        for fact in fact_by_key.get(key, ()):
            period = str(fact.get("period_end") or fact.get("period") or "")
            if period and _numeric_value(fact) is not None:
                grouped.setdefault(_chart_signature(fact), {}).setdefault(period, []).append(fact)
        for by_period in grouped.values():
            if any(len(items) != 1 for items in by_period.values()):
                continue
            series = [items[0] for _, items in sorted(by_period.items())]
            if len(series) >= 2 and (best is None or len(series) > len(best[1])):
                best = (key, series)
    if best is None:
        return None
    key, series = best
    return {
        "chart_id": "core_metric_trend",
        "kind": "trend",
        "title": f"{METRIC_LABELS.get(key, key)}趋势",
        "metric_key": key,
        "currency": str(series[-1].get("currency") or ""),
        "points": [
            {
                "period": str(fact.get("period_end") or fact.get("period") or ""),
                "value": _numeric_value(fact),
                "formatted_value": _format_fact(fact),
                "fact_id": str(fact.get("fact_id") or ""),
                "evidence_ids": _fact_evidence_ids(fact),
            }
            for fact in series
        ],
    }


def _same_period_facts(
    fact_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
    key_groups: Sequence[Sequence[str]],
) -> list[Mapping[str, Any]] | None:
    candidates = [
        [item for key in keys for item in fact_by_key.get(key, ()) if _numeric_value(item) is not None]
        for keys in key_groups
    ]
    if any(not items for items in candidates):
        return None
    periods = set(str(item.get("period_end") or item.get("period") or "") for item in candidates[0])
    for items in candidates[1:]:
        periods &= {str(item.get("period_end") or item.get("period") or "") for item in items}
    for period in sorted((item for item in periods if item), reverse=True):
        selected: list[Mapping[str, Any]] = []
        for items in candidates:
            matches = [item for item in items if str(item.get("period_end") or item.get("period") or "") == period]
            if len(matches) != 1:
                break
            selected.append(matches[0])
        if len(selected) == len(key_groups) and all(
            _facts_comparable(selected[0], item, require_distinct_period=False)
            and _chart_signature(selected[0]) == _chart_signature(item)
            for item in selected[1:]
        ):
            return selected
    return None


def _structure_visual(fact_by_key: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any] | None:
    selected = _same_period_facts(fact_by_key, (("total_assets",), ("total_liabilities",)))
    if not selected:
        return None
    assets, liabilities = selected
    items = [assets, liabilities]
    equity = _same_period_facts(fact_by_key, (("total_assets",), ("total_equity",)))
    if equity and str(equity[0].get("fact_id")) == str(assets.get("fact_id")):
        items.append(equity[1])
    return {
        "chart_id": "balance_sheet_structure",
        "kind": "structure",
        "title": "资产负债结构",
        "period": str(assets.get("period_end") or assets.get("period") or ""),
        "currency": str(assets.get("currency") or ""),
        "items": [
            {
                "metric_key": str(fact.get("metric_key") or ""),
                "label": METRIC_LABELS.get(str(fact.get("metric_key") or ""), str(fact.get("raw_label") or "")),
                "value": _numeric_value(fact),
                "formatted_value": _format_fact(fact),
                "fact_id": str(fact.get("fact_id") or ""),
                "evidence_ids": _fact_evidence_ids(fact),
            }
            for fact in items
        ],
    }


def _profit_bridge_visual(fact_by_key: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any] | None:
    selected = _same_period_facts(
        fact_by_key,
        (
            ("revenue", "operating_revenue", "total_revenue"),
            ("operating_profit", "operating_income"),
            ("parent_net_profit", "net_profit_parent", "net_profit", "net_income"),
        ),
    )
    if not selected:
        return None
    revenue, operating_profit, net_profit = selected
    values = [_numeric_value(item) for item in selected]
    if any(item is None for item in values):
        return None
    revenue_value, operating_value, profit_value = (float(item) for item in values)
    evidence_ids = list(dict.fromkeys(item for fact in selected for item in _fact_evidence_ids(fact)))
    return {
        "chart_id": "profit_bridge",
        "kind": "profit_bridge",
        "title": "盈利桥",
        "period": str(revenue.get("period_end") or revenue.get("period") or ""),
        "currency": str(revenue.get("currency") or ""),
        "bars": [
            {
                "label": "营业收入",
                "start": 0.0,
                "end": revenue_value,
                "value": revenue_value,
                "role": "total",
                "evidence_ids": _fact_evidence_ids(revenue),
            },
            {
                "label": "经营成本及其他净影响",
                "start": revenue_value,
                "end": operating_value,
                "value": operating_value - revenue_value,
                "role": "delta",
                "evidence_ids": evidence_ids,
            },
            {
                "label": "税费与非经营净影响",
                "start": operating_value,
                "end": profit_value,
                "value": profit_value - operating_value,
                "role": "delta",
                "evidence_ids": evidence_ids,
            },
            {
                "label": "归母/净利润",
                "start": 0.0,
                "end": profit_value,
                "value": profit_value,
                "role": "total",
                "evidence_ids": _fact_evidence_ids(net_profit),
            },
        ],
        "note": "桥接差额仅由同期间、同币种、同口径的披露小计计算，不拆分未解析的成本项目。",
    }


def _build_visuals(
    fact_by_key: Mapping[str, Sequence[Mapping[str, Any]]],
    entity_profile: Mapping[str, Any],
) -> list[dict[str, Any]]:
    visuals = [
        _trend_visual(fact_by_key, entity_profile),
        _structure_visual(fact_by_key),
        _profit_bridge_visual(fact_by_key),
    ]
    return [item for item in visuals if item]


def _section_disclosure(catalog: Sequence[Any], role: str) -> str:
    matches = [item for item in catalog if isinstance(item, Mapping) and item.get("role") == role]
    if not matches:
        return ""
    role_label = {
        "business": "业务概览",
        "mda": "管理层讨论与分析",
        "segments": "分部信息",
        "risk_factors": "风险因素",
        "market_risk": "市场风险",
        "controls": "控制与治理",
        "notes": "财务报表附注",
    }.get(role, "相关披露")
    return (
        f"当前解析包已定位 {len(matches)} 处{role_label}材料；正文仅作主题导航，具体事实和结论须回到对应原文定位核验。"
    )


def _evidence_search_text(evidence: Mapping[str, Any]) -> str:
    return " ".join(
        str(evidence.get(key) or "").lower()
        for key in ("section_id", "section_role", "local_source_id", "html_anchor", "quote", "target")
    )


def _format_fact_value(fact: Mapping[str, Any] | None) -> str:
    if not fact:
        return "数据不可用"
    value = fact.get("normalized_value") if fact.get("normalized_value") is not None else fact.get("value")
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = None
    currency = str(fact.get("currency") or "").strip().upper()
    unit = str(fact.get("unit") or "").strip()
    if number is None or not math.isfinite(number):
        formatted = str(value if value not in (None, "") else "数据不可用")
    elif currency:
        base_value = number
        divisor, label = _display_scale(base_value)
        formatted = f"{base_value / divisor:,.2f} {currency} {label}".strip()
    else:
        formatted = f"{number:,.2f}{unit}"
    return formatted


def _format_fact(fact: Mapping[str, Any] | None) -> str:
    formatted = _format_fact_value(fact)
    if not fact:
        return formatted
    period = str(fact.get("period_end") or fact.get("period") or "").strip()
    return f"{formatted}（{period}）" if period else formatted


def _display_scale(value: float) -> tuple[float, str]:
    absolute = abs(value)
    if absolute >= 1_000_000_000:
        return 1_000_000_000.0, "billion"
    if absolute >= 1_000_000:
        return 1_000_000.0, "million"
    if absolute >= 1_000:
        return 1_000.0, "thousand"
    return 1.0, ""


def _render_markdown(report: Mapping[str, Any]) -> str:
    source = report["source_report"]
    identity = report["research_identity"]
    presentation_evidence = _presentation_evidence(report)
    evidence_by_id = {_evidence_id(item): item for item in presentation_evidence}
    lines = [
        f"# {report['title']}",
        "",
        f"- 市场：{identity['market']}",
        f"- 公司：{report['company'].get('display_code') or ''} {report['company'].get('display_name') or ''}".rstrip(),
        f"- 源报告：{source.get('report_id')}",
        f"- 报告期：{source.get('period_end') or source.get('fiscal_year') or '未披露'}",
        f"- 适配器：{report['adapter'].get('source_family')}@{report['adapter'].get('version')}",
        f"- 质量状态：{report['quality'].get('status') or 'unknown'}",
        "",
    ]
    for index, section in enumerate(report["sections"], 1):
        lines.extend([f"## {index}. {section['title']}", ""])
        lines.extend(f"- {item}" for item in section["content"])
        if section["evidence_ids"]:
            citations = []
            for evidence_id in section["evidence_ids"][:4]:
                evidence = evidence_by_id.get(str(evidence_id))
                if evidence is None:
                    continue
                label = _evidence_semantic_label(evidence).replace("|", "\\|")
                citations.append(f"[{label}](#{_evidence_anchor(str(evidence_id))})")
            if citations:
                lines.append("- 关键证据：" + "；".join(citations))
        lines.append("")
    lines.extend(["## 核心事实", "", "| 指标 | 数值 | 期间 | 证据 |", "| --- | ---: | --- | --- |"])
    displayed_facts = [fact for fact in report["facts"] if _fact_is_analysis_eligible(fact)][:80]
    for fact in displayed_facts:
        label = METRIC_LABELS.get(
            str(fact.get("metric_key")), str(fact.get("raw_label") or fact.get("metric_key") or "未命名")
        )
        value = _format_fact_value(fact)
        period = str(fact.get("period_end") or fact.get("period") or "")
        citations = []
        for evidence_id in _fact_evidence_ids(fact)[:2]:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is not None:
                evidence_label = _evidence_semantic_label(evidence).replace("|", "\\|")
                citations.append(f"[{evidence_label}](#{_evidence_anchor(evidence_id)})")
        lines.append(f"| {label} | {value} | {period} | {'；'.join(citations) or '详见结构化附件'} |")
    if not displayed_facts:
        lines.append("| 当前无符合核心指标资格的结构化事实 | - | - | 详见完整证据目录 |")
    ordered_evidence = presentation_evidence
    total_evidence_count = len(_ordered_evidence(report))
    lines.extend(
        [
            "",
            "## 核心结论证据目录",
            "",
            '<details class="evidence-catalog">',
            (
                f"<summary>展开核心结论证据（{len(ordered_evidence)} 条，默认折叠；"
                f"全部 {total_evidence_count} 条见 JSON 结构化附件）</summary>"
            ),
            "",
        ]
    )
    for group_label, group_items in _group_evidence(ordered_evidence):
        lines.extend([f"### {group_label}", ""])
        for evidence in group_items:
            evidence_id = _evidence_id(evidence)
            href = _evidence_href(evidence)
            label = _evidence_semantic_label(evidence)
            source_link = f'<a href="{html.escape(href, quote=True)}">查看原始定位</a>' if href else "原始定位未提供"
            excerpt = html.escape(_evidence_excerpt(evidence))
            lines.append(
                f'<div id="{html.escape(_evidence_anchor(evidence_id), quote=True)}" class="evidence-reference">'
                f"<strong>{html.escape(label)}</strong> · {source_link}"
                f"<br><small>审计编号：{html.escape(evidence_id)}</small>"
                + (f"<blockquote>{excerpt}</blockquote>" if excerpt else "")
                + "</div>"
            )
            lines.append("")
    lines.extend(["</details>", ""])
    lines.extend(["", report["disclaimer"], ""])
    return "\n".join(lines)


def _evidence_anchor(evidence_id: str) -> str:
    safe = "".join(character if character.isalnum() or character in "-_" else "-" for character in evidence_id)
    return f"evidence-{safe}"


def _render_evidence_links(
    evidence_ids: Sequence[Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    *,
    limit: int = 4,
) -> str:
    links: list[str] = []
    for raw_id in evidence_ids:
        evidence_id = str(raw_id or "").strip()
        evidence = evidence_by_id.get(evidence_id)
        if not evidence_id or evidence is None:
            continue
        label = _evidence_semantic_label(evidence)
        links.append(
            f'<a class="evidence-link" href="#{html.escape(_evidence_anchor(evidence_id), quote=True)}">'
            f"{html.escape(label)}</a>"
        )
        if len(links) >= limit:
            break
    return "".join(links)


def _format_chart_amount(value: float, currency: str) -> str:
    divisor, scale_label = _display_scale(value)
    amount = f"{value / divisor:,.2f}"
    return " ".join(item for item in (amount, currency.strip().upper(), scale_label) if item)


def _svg_link(
    content: str,
    evidence_ids: Sequence[Any],
    label: str,
    allowed_evidence_ids: set[str] | frozenset[str],
) -> str:
    evidence_id = next(
        (str(item) for item in evidence_ids if item and str(item) in allowed_evidence_ids),
        "",
    )
    if not evidence_id:
        return content
    return (
        f'<a href="#{html.escape(_evidence_anchor(evidence_id), quote=True)}" '
        f'aria-label="{html.escape(label, quote=True)}">{content}</a>'
    )


def _render_trend_svg(
    visual: Mapping[str, Any],
    allowed_evidence_ids: set[str] | frozenset[str],
) -> str:
    points = [item for item in visual.get("points") or () if isinstance(item, Mapping)]
    values = [float(item["value"]) for item in points]
    low, high = min(values), max(values)
    spread = high - low or max(abs(high), 1.0)
    x_step = 540.0 / max(len(points) - 1, 1)
    coordinates = [
        (90.0 + index * x_step, 238.0 - (value - low) / spread * 150.0) for index, value in enumerate(values)
    ]
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in coordinates)
    marks: list[str] = []
    for point, (x, y) in zip(points, coordinates, strict=True):
        period = str(point.get("period") or "")
        value_label = str(point.get("formatted_value") or "")
        mark = (
            f'<g class="chart-mark" tabindex="0"><circle cx="{x:.1f}" cy="{y:.1f}" r="6">'
            f"<title>{html.escape(period + ' ' + value_label)}</title></circle>"
            f'<text x="{x:.1f}" y="{y - 13:.1f}" text-anchor="middle">{html.escape(_format_chart_amount(float(point["value"]), str(visual.get("currency") or "")))}</text>'
            f'<text class="axis-label" x="{x:.1f}" y="274" text-anchor="middle">{html.escape(period)}</text></g>'
        )
        marks.append(
            _svg_link(
                mark,
                point.get("evidence_ids") or (),
                f"{period} {value_label}",
                allowed_evidence_ids,
            )
        )
    return (
        f'<svg class="financial-chart" data-chart-kind="trend" data-chart-metric="{html.escape(str(visual.get("metric_key") or ""), quote=True)}" '
        f'viewBox="0 0 720 310" role="img" aria-labelledby="trend-title trend-desc">'
        f'<title id="trend-title">{html.escape(str(visual.get("title") or "趋势图"))}</title>'
        '<desc id="trend-desc">同币种、同口径、不同报告期间的披露指标趋势。</desc>'
        '<line class="chart-axis" x1="74" y1="246" x2="650" y2="246"/>'
        '<line class="chart-axis" x1="74" y1="76" x2="74" y2="246"/>'
        f'<polyline class="trend-line" points="{polyline}"/>{"".join(marks)}</svg>'
    )


def _render_structure_svg(
    visual: Mapping[str, Any],
    allowed_evidence_ids: set[str] | frozenset[str],
) -> str:
    items = [item for item in visual.get("items") or () if isinstance(item, Mapping)]
    maximum = max(abs(float(item["value"])) for item in items) or 1.0
    rows: list[str] = []
    colors = ("#1769e0", "#e0a126", "#d14c45")
    for index, item in enumerate(items):
        y = 78 + index * 72
        width = max(abs(float(item["value"])) / maximum * 430.0, 2.0)
        label = str(item.get("label") or item.get("metric_key") or "")
        value_label = str(item.get("formatted_value") or "")
        mark = (
            f'<g class="chart-mark" tabindex="0"><text x="70" y="{y + 17}" text-anchor="end">{html.escape(label)}</text>'
            f'<rect x="86" y="{y}" width="{width:.1f}" height="26" rx="2" fill="{colors[index % len(colors)]}">'
            f"<title>{html.escape(label + ' ' + value_label)}</title></rect>"
            f'<text x="{min(86 + width + 10, 650):.1f}" y="{y + 18}">{html.escape(value_label)}</text></g>'
        )
        rows.append(
            _svg_link(
                mark,
                item.get("evidence_ids") or (),
                f"{label} {value_label}",
                allowed_evidence_ids,
            )
        )
    return (
        '<svg class="financial-chart" data-chart-kind="structure" viewBox="0 0 720 310" role="img" aria-labelledby="structure-title structure-desc">'
        f'<title id="structure-title">{html.escape(str(visual.get("title") or "结构图"))}</title>'
        f'<desc id="structure-desc">{html.escape(str(visual.get("period") or ""))} 同币种资产负债披露结构。</desc>'
        f"{''.join(rows)}</svg>"
    )


def _render_profit_bridge_svg(
    visual: Mapping[str, Any],
    allowed_evidence_ids: set[str] | frozenset[str],
) -> str:
    bars = [item for item in visual.get("bars") or () if isinstance(item, Mapping)]
    levels = [0.0, *[float(item["start"]) for item in bars], *[float(item["end"]) for item in bars]]
    low, high = min(levels), max(levels)
    spread = high - low or max(abs(high), 1.0)

    def y_coordinate(value: float) -> float:
        return 242.0 - (value - low) / spread * 164.0

    marks: list[str] = []
    short_labels = ("收入", "经营净影响", "税费/非经营", "净利润")
    for index, item in enumerate(bars):
        x = 68 + index * 162
        start, end, value = float(item["start"]), float(item["end"]), float(item["value"])
        top = min(y_coordinate(start), y_coordinate(end))
        height = max(abs(y_coordinate(start) - y_coordinate(end)), 2.0)
        role = str(item.get("role") or "delta")
        color = "#1769e0" if role == "total" else "#d14c45" if value < 0 else "#e0a126"
        value_label = _format_chart_amount(value, str(visual.get("currency") or ""))
        full_label = str(item.get("label") or short_labels[index])
        mark = (
            f'<g class="chart-mark" tabindex="0"><rect x="{x}" y="{top:.1f}" width="92" height="{height:.1f}" rx="2" fill="{color}">'
            f"<title>{html.escape(full_label + ' ' + value_label)}</title></rect>"
            f'<text x="{x + 46}" y="{max(top - 9, 20):.1f}" text-anchor="middle">{html.escape(value_label)}</text>'
            f'<text class="axis-label" x="{x + 46}" y="274" text-anchor="middle">{html.escape(short_labels[index])}</text></g>'
        )
        marks.append(
            _svg_link(
                mark,
                item.get("evidence_ids") or (),
                f"{full_label} {value_label}",
                allowed_evidence_ids,
            )
        )
    return (
        '<svg class="financial-chart" data-chart-kind="profit_bridge" viewBox="0 0 720 310" role="img" aria-labelledby="bridge-title bridge-desc">'
        f'<title id="bridge-title">{html.escape(str(visual.get("title") or "盈利桥"))}</title>'
        f'<desc id="bridge-desc">{html.escape(str(visual.get("note") or ""))}</desc>'
        f'<line class="chart-axis" x1="50" y1="{y_coordinate(0):.1f}" x2="690" y2="{y_coordinate(0):.1f}"/>{"".join(marks)}</svg>'
    )


def _render_visual_summary(
    report: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> str:
    kpis = [item for item in report.get("kpis") or () if isinstance(item, Mapping)]
    visuals = [item for item in report.get("visuals") or () if isinstance(item, Mapping)]
    if not kpis and not visuals:
        return ""
    kpi_html = "".join(
        (
            f'<div class="kpi" data-metric="{html.escape(str(item.get("metric_key") or ""), quote=True)}">'
            f"<span>{html.escape(str(item.get('label') or ''))}</span><strong>{html.escape(str(item.get('value') or ''))}</strong>"
            f"{_render_evidence_links(item.get('evidence_ids') or (), evidence_by_id, limit=1)}</div>"
        )
        for item in kpis
    )
    chart_html: list[str] = []
    allowed_evidence_ids = frozenset(evidence_by_id)
    for visual in visuals:
        kind = str(visual.get("kind") or "")
        svg = (
            _render_trend_svg(visual, allowed_evidence_ids)
            if kind == "trend"
            else _render_structure_svg(visual, allowed_evidence_ids)
            if kind == "structure"
            else _render_profit_bridge_svg(visual, allowed_evidence_ids)
            if kind == "profit_bridge"
            else ""
        )
        if svg:
            chart_html.append(
                f'<figure class="chart-figure"><figcaption>{html.escape(str(visual.get("title") or "财务图表"))}</figcaption>{svg}'
                f"<p>{html.escape(str(visual.get('note') or ''))}</p></figure>"
            )
    return (
        '<section class="visual-summary" aria-label="关键财务可视化"><div class="section-title"><span>00</span><h2>关键指标与财务图表</h2><b>evidence-bound</b></div>'
        f'<div class="kpi-grid">{kpi_html}</div><div class="chart-grid">{"".join(chart_html)}</div></section>'
    )


def _status_label(status: Any) -> str:
    return {
        "ready": "就绪",
        "pass": "通过",
        "completed": "完成",
        "degraded": "降级",
        "warning": "有告警",
        "unavailable": "不可用",
        "not_applicable": "不适用",
        "unknown": "未标注",
    }.get(str(status or "").lower(), str(status or "未标注"))


def _render_evidence_catalog_html(report: Mapping[str, Any]) -> str:
    ordered = _presentation_evidence(report)
    total_count = len(_ordered_evidence(report))
    groups: list[str] = []
    for group_label, group_items in _group_evidence(ordered):
        references: list[str] = []
        for evidence in group_items:
            evidence_id = _evidence_id(evidence)
            semantic_label = _evidence_semantic_label(evidence)
            href = _evidence_href(evidence)
            source_link = (
                f'<a class="source-link" href="{html.escape(href, quote=True)}" '
                f'target="_blank" rel="noopener noreferrer">查看原始定位</a>'
                if href
                else '<span class="source-unavailable">原始定位未提供</span>'
            )
            excerpt = _evidence_excerpt(evidence)
            references.append(
                f'<article class="evidence-reference" id="{html.escape(_evidence_anchor(evidence_id), quote=True)}">'
                f'<div class="evidence-reference-head"><strong>{html.escape(semantic_label)}</strong>{source_link}</div>'
                f'<small class="audit-id">审计编号：{html.escape(evidence_id)}</small>'
                + (f"<blockquote>{html.escape(excerpt)}</blockquote>" if excerpt else "")
                + "</article>"
            )
        groups.append(f'<div class="evidence-group"><h3>{html.escape(group_label)}</h3>{"".join(references)}</div>')
    return (
        '<details class="evidence-catalog">'
        + (
            f"<summary>展开核心结论证据（{len(ordered)} 条，默认折叠；"
            f"全部 {total_count} 条见 JSON 结构化附件）</summary>"
        )
        + f'<div class="evidence-catalog-body">{"".join(groups)}</div></details>'
    )


def _render_html(report: Mapping[str, Any]) -> str:
    presentation_evidence = _presentation_evidence(report)
    evidence_by_id = {_evidence_id(item): item for item in presentation_evidence}
    markdown_sections = []
    for index, section in enumerate(report["sections"], 1):
        status = html.escape(str(section["status"]), quote=True)
        status_label = html.escape(_status_label(section["status"]))
        items = "".join(f"<li>{html.escape(str(item))}</li>" for item in section["content"])
        citations = _render_evidence_links(section["evidence_ids"], evidence_by_id)
        markdown_sections.append(
            f'<section id="{html.escape(section["section_id"])}"><div class="section-title"><span>{index:02d}</span><h2>{html.escape(section["title"])}</h2><b data-status="{status}">{status_label}</b></div><ul>{items}</ul><div class="citations" aria-label="本章关键证据">{citations}</div></section>'
        )
    fact_rows = []
    for fact in (item for item in report["facts"] if _fact_is_analysis_eligible(item)):
        if len(fact_rows) >= 80:
            break
        label = METRIC_LABELS.get(
            str(fact.get("metric_key")), str(fact.get("raw_label") or fact.get("metric_key") or "未命名")
        )
        evidence_ids = _fact_evidence_ids(fact)
        fact_rows.append(
            f"<tr><th>{html.escape(label)}</th><td>{html.escape(_format_fact(fact))}</td><td>{_render_evidence_links(evidence_ids, evidence_by_id, limit=2) or '详见结构化附件'}</td></tr>"
        )
    if not fact_rows:
        fact_rows.append('<tr><td colspan="3">当前没有符合核心指标资格的结构化事实，数值结论保持降级。</td></tr>')
    evidence_catalog = _render_evidence_catalog_html(report)
    identity = report["research_identity"]
    source = report["source_report"]
    quality = _status_label(report["quality"].get("status") or "unknown")
    policy_market = report.get("market_policy", {}).get("market", {})
    market_label = str(policy_market.get("label") or identity["market"])
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(report["title"])}</title><style>
:root{{--ink:#172033;--muted:#607089;--line:#d9e2ee;--blue:#1769e0;--warn:#a45b00;--bg:#f4f7fb;--chart-grid:#cbd6e3}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.75 system-ui,-apple-system,"Segoe UI",sans-serif;letter-spacing:0}}main{{max-width:1120px;margin:auto;background:#fff;min-height:100vh;padding:56px clamp(24px,6vw,80px);overflow:hidden}}header{{border-bottom:2px solid var(--ink);padding-bottom:28px}}h1{{font-family:Georgia,"Noto Serif SC",serif;font-size:38px;margin:0 0 12px}}.meta{{display:flex;flex-wrap:wrap;gap:8px 22px;color:var(--muted)}}.quality{{display:inline-block;margin-top:18px;padding:4px 10px;border:1px solid #e2b16d;color:var(--warn);border-radius:4px}}section{{padding:30px 0;border-bottom:1px solid var(--line)}}.section-title{{display:grid;grid-template-columns:36px 1fr auto;align-items:center;gap:12px}}.section-title span{{color:var(--blue);font-weight:700}}h2{{font-size:21px;margin:0}}h3{{font-size:16px;margin:24px 0 8px}}.section-title b{{font-size:12px;color:var(--muted);font-weight:600}}li{{margin:8px 0}}.citations{{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}}.citations:empty{{display:none}}.evidence-link{{color:var(--blue);text-decoration:none;border-bottom:1px solid #9fc4f7}}.evidence-link:focus-visible,.chart-mark:focus-visible,summary:focus-visible{{outline:3px solid #e0a126;outline-offset:3px}}.kpi-grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin-top:24px;border-block:1px solid var(--line)}}.kpi{{min-width:0;padding:18px 16px;border-left:1px solid var(--line)}}.kpi:first-child{{border-left:0}}.kpi span,.kpi strong{{display:block}}.kpi span{{color:var(--muted)}}.kpi strong{{font-size:18px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}}.kpi .evidence-link{{display:inline-block;margin-top:6px;font-size:13px}}.chart-grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;margin-top:28px}}.chart-figure{{min-width:0;margin:0;border:1px solid var(--line);border-radius:4px;padding:16px;background:#fff}}.chart-figure figcaption{{font-size:16px;font-weight:700}}.chart-figure p{{margin:6px 0 0;color:var(--muted);font-size:13px}}.financial-chart{{display:block;width:100%;height:auto;aspect-ratio:720/310;min-height:250px}}.financial-chart text{{fill:var(--ink);font:12px system-ui,-apple-system,"Segoe UI",sans-serif;letter-spacing:0}}.financial-chart .axis-label{{fill:var(--muted)}}.chart-axis{{stroke:var(--chart-grid);stroke-width:1;vector-effect:non-scaling-stroke}}.trend-line{{fill:none;stroke:var(--blue);stroke-width:3;vector-effect:non-scaling-stroke}}.chart-mark circle{{fill:#fff;stroke:var(--blue);stroke-width:3;vector-effect:non-scaling-stroke}}table{{width:100%;border-collapse:collapse;margin-top:20px}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}details.evidence-catalog{{margin-top:18px;border-block:1px solid var(--line)}}details.evidence-catalog>summary{{cursor:pointer;padding:16px 0;font-weight:700;color:var(--blue)}}.evidence-catalog-body{{padding:0 0 18px}}.evidence-reference{{padding:14px 0;border-bottom:1px solid var(--line);scroll-margin-top:18px}}.evidence-reference-head{{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}}.source-link{{color:var(--blue);white-space:nowrap}}.source-unavailable,.audit-id{{color:var(--muted)}}.audit-id{{display:block;margin-top:4px;overflow-wrap:anywhere}}blockquote{{margin:9px 0 0;padding-left:12px;border-left:2px solid var(--line);color:var(--muted)}}footer{{margin-top:40px;color:var(--muted)}}@media(max-width:760px){{.chart-grid{{grid-template-columns:1fr}}.kpi-grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}.kpi:nth-child(odd){{border-left:0}}}}@media(max-width:640px){{main{{padding:30px 18px}}h1{{font-size:30px}}.section-title{{grid-template-columns:30px 1fr}}.section-title b{{grid-column:2}}.chart-figure{{padding:8px}}.financial-chart{{min-height:210px}}.evidence-reference-head{{display:block}}.source-link,.source-unavailable{{display:inline-block;margin-top:6px}}table{{display:block;max-width:100%;overflow-x:auto}}}}
</style></head><body><main><header><h1>{html.escape(report["title"])}</h1><div class="meta"><span>{html.escape(market_label)}</span><span>{html.escape(str(report["company"].get("display_code") or ""))}</span><span>{html.escape(str(source.get("report_id") or ""))}</span><span>截止 {html.escape(str(source.get("period_end") or "未披露"))}</span><span>{html.escape(str(report["adapter"].get("source_family")))}</span></div><div class="quality">源数据质量：{html.escape(quality)}</div></header>{_render_visual_summary(report, evidence_by_id)}{"".join(markdown_sections)}<section><div class="section-title"><span>15</span><h2>核心事实表</h2><b>可回溯</b></div><table><tbody>{"".join(fact_rows)}</tbody></table></section><section><div class="section-title"><span>16</span><h2>核心结论证据</h2><b>只读审计</b></div><p>正文仅保留核心结论使用的可读定位摘要；全部适配器证据保存在 JSON 结构化附件中。</p>{evidence_catalog}</section><footer>{html.escape(report["disclaimer"])}</footer></main><script>document.addEventListener("click",function(event){{var link=event.target.closest('a[href^="#evidence-"]');if(!link)return;var target=document.querySelector(link.getAttribute("href"));if(!target)return;var catalog=target.closest("details");if(catalog)catalog.open=true;}});</script></body></html>"""


def _validate_rendered_report(
    report: Mapping[str, Any],
    markdown: str,
    html_text: str,
    sidecar: Mapping[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    identity = report.get("research_identity") if isinstance(report.get("research_identity"), Mapping) else {}
    if any(not str(identity.get(field) or "") for field in ("market", "company_id", "filing_id", "parse_run_id")):
        failures.append("research_identity_incomplete")
    if len(report.get("sections") or ()) != len(SECTION_SPECS):
        failures.append("section_count_invalid")
    market_policy = report.get("market_policy") if isinstance(report.get("market_policy"), Mapping) else {}
    policy_market = market_policy.get("market") if isinstance(market_policy.get("market"), Mapping) else {}
    if market_policy.get("schema_version") != POLICY_SCHEMA_VERSION:
        failures.append("market_policy_schema_invalid")
    if str(policy_market.get("code") or "") != str(identity.get("market") or ""):
        failures.append("market_policy_identity_mismatch")
    policy_sections = market_policy.get("sections") if isinstance(market_policy.get("sections"), Mapping) else {}
    for chapter_id in MARKET_POLICY_CHAPTER_IDS:
        insights = policy_sections.get(chapter_id)
        if not isinstance(insights, list) or len(insights) < 2:
            failures.append("market_policy_chapter_incomplete")
            continue
        if any(
            not isinstance(item, Mapping)
            or not str(item.get("text") or "").strip()
            or not str(item.get("basis") or "").strip()
            or not str(item.get("scope") or "").strip()
            for item in insights
        ):
            failures.append("market_policy_insight_invalid")
    if report.get("adapter", {}).get("source_family") == "sec_ixbrl":
        if "pdf_page" in json.dumps(report.get("evidence_refs") or (), ensure_ascii=False):
            failures.append("sec_pdf_locator_forbidden")
        if not any(
            item.get("html_anchor") or item.get("xbrl_fact_id") or item.get("xbrl_concept")
            for item in report.get("evidence_refs") or ()
            if isinstance(item, Mapping)
        ):
            failures.append("sec_locator_missing")
    research_pack = report.get("research_pack") if isinstance(report.get("research_pack"), Mapping) else {}
    if research_pack.get("validation_status") != "pass" or len(research_pack.get("agent_ids") or ()) < 5:
        failures.append("research_pack_validation_missing")
    evidence_by_id = {
        _evidence_id(item): item
        for item in report.get("evidence_refs") or ()
        if isinstance(item, Mapping)
    }
    evidence_ids = set(evidence_by_id)
    claims = [item for item in report.get("claims") or () if isinstance(item, Mapping)]
    claim_evidence_ids = {
        str(evidence_id)
        for claim in claims
        for evidence_id in claim.get("evidence_ids") or ()
        if evidence_id
    }
    presentation_evidence = _presentation_evidence(report)
    presentation_evidence_ids = {_evidence_id(item) for item in presentation_evidence}
    if '<details class="evidence-catalog"' not in html_text:
        failures.append("evidence_catalog_missing")
    if '<details open class="evidence-catalog"' in html_text or '<details class="evidence-catalog" open' in html_text:
        failures.append("evidence_catalog_not_collapsed")
    if len(claim_evidence_ids) > MAX_PRESENTATION_EVIDENCE_ITEMS:
        failures.append("evidence_catalog_claim_limit_exceeded")
    if not claim_evidence_ids.issubset(presentation_evidence_ids):
        failures.append("evidence_catalog_claims_incomplete")
    if any(not _evidence_has_locator(evidence_by_id[evidence_id]) for evidence_id in claim_evidence_ids if evidence_id in evidence_by_id):
        failures.append("claim_evidence_locator_missing")
    rendered_catalog_ids = set(
        re.findall(
            r'<article\b[^>]*class="[^"]*\bevidence-reference\b[^"]*"[^>]*id="(evidence-[^"]+)"',
            html_text,
        )
    )
    expected_catalog_ids = {_evidence_anchor(evidence_id) for evidence_id in presentation_evidence_ids}
    if rendered_catalog_ids != expected_catalog_ids:
        failures.append("evidence_catalog_claims_incomplete")
    if len(rendered_catalog_ids) > MAX_PRESENTATION_EVIDENCE_ITEMS:
        failures.append("evidence_catalog_overloaded")
    if len(html_text.encode("utf-8")) > MAX_PRESENTATION_HTML_BYTES:
        failures.append("presentation_html_too_large")
    if _html_has_bare_evidence_id_link(html_text):
        failures.append("bare_evidence_id_link_forbidden")
    for claim in claims:
        if any(not str(claim.get(field) or "").strip() for field in ("claim_id", "claim", "claim_type")):
            failures.append("analysis_claim_invalid")
        claim_evidence_ids = {str(item) for item in claim.get("evidence_ids") or () if item}
        if claim_evidence_ids and not claim_evidence_ids.issubset(evidence_ids):
            failures.append("analysis_claim_evidence_unknown")
    for section in report.get("sections") or ():
        if not isinstance(section, Mapping):
            continue
        pack_refs = section.get("research_pack_refs") if isinstance(section.get("research_pack_refs"), Mapping) else {}
        expected_agent = {
            "executive_summary": "financial_modeler",
            "revenue_quality": "financial_modeler",
            "profitability": "financial_modeler",
            "balance_sheet": "financial_modeler",
            "cash_flow": "financial_modeler",
            "capital_allocation": "financial_modeler",
            "business_overview": "business_strategy_researcher",
            "segments": "business_strategy_researcher",
            "risk_factors": "governance_risk_researcher",
            "controls": "governance_risk_researcher",
            "accounting_quality": "governance_risk_researcher",
            "tracking": "governance_risk_researcher",
        }.get(str(section.get("section_id") or ""))
        if expected_agent and expected_agent not in (pack_refs.get("agent_ids") or ()):
            failures.append("section_research_pack_provenance_missing")
        for evidence_id in section.get("evidence_ids") or ():
            if str(evidence_id) not in evidence_ids:
                failures.append("section_evidence_unknown")
    for fact in report.get("facts") or ():
        if not isinstance(fact, Mapping):
            continue
        if (
            str(fact.get("metric_key") or "")
            in {
                "revenue",
                "operating_revenue",
                "total_revenue",
                "net_income",
                "net_profit",
                "parent_net_profit",
                "net_profit_parent",
                "operating_cash_flow",
                "operating_cash_flow_net",
                "net_operating_cash_flow",
                "total_assets",
                "total_liabilities",
                "net_interest_income",
                "net_interest_margin",
                "capital_adequacy_ratio",
                "core_tier_1_capital_adequacy_ratio",
                "tier_1_capital_ratio",
                "insurance_revenue",
                "insurance_service_result",
                "solvency_ratio",
            }
            and _numeric_value(fact) is not None
            and not _fact_evidence_ids(fact)
        ):
            failures.append("core_fact_evidence_missing")
    visual_evidence_ids: list[str] = []
    for kpi in report.get("kpis") or ():
        if isinstance(kpi, Mapping):
            visual_evidence_ids.extend(str(item) for item in kpi.get("evidence_ids") or () if item)
    for visual in report.get("visuals") or ():
        if not isinstance(visual, Mapping):
            continue
        entries = visual.get("points") or visual.get("items") or visual.get("bars") or ()
        for item in entries:
            if isinstance(item, Mapping):
                visual_evidence_ids.extend(str(value) for value in item.get("evidence_ids") or () if value)
        if visual.get("kind") == "trend" and len(visual.get("points") or ()) < 2:
            failures.append("trend_chart_periods_insufficient")
    if any(item not in evidence_ids for item in visual_evidence_ids):
        failures.append("visual_evidence_unknown")
    if report.get("visuals"):
        if 'class="financial-chart"' not in html_text or 'role="img"' not in html_text:
            failures.append("financial_chart_missing")
        if presentation_evidence_ids.intersection(visual_evidence_ids) and 'href="#evidence-' not in html_text:
            failures.append("visual_evidence_link_missing")
        if "@media(max-width:760px)" not in html_text:
            failures.append("visual_responsive_rule_missing")
    target = sidecar.get("research_target") if isinstance(sidecar.get("research_target"), Mapping) else {}
    expected_identity = target.get("research_identity")
    if expected_identity != identity:
        failures.append("sidecar_identity_mismatch")
    if not markdown.strip() or "<html" not in html_text:
        failures.append("rendered_content_missing")
    return {"ok": not failures, "status": "pass" if not failures else "fail", "failures": failures}


def _validate_shared_contracts(report: Mapping[str, Any], sidecar: Mapping[str, Any]) -> None:
    project_root = Path(os.environ.get("SIQ_PROJECT_ROOT", Path(__file__).resolve().parents[5])).resolve()
    contracts_src = project_root / "packages" / "market-contracts" / "src"
    inserted = False
    if contracts_src.is_dir() and str(contracts_src) not in sys.path:
        sys.path.insert(0, str(contracts_src))
        inserted = True
    try:
        from siq_market_contracts import AgentArtifactV2, EvidenceRefV1, NormalizedFactV1

        for evidence in report.get("evidence_refs") or ():
            EvidenceRefV1.from_dict(evidence)
        for fact in report.get("facts") or ():
            NormalizedFactV1.from_dict(fact)
        AgentArtifactV2.from_dict(sidecar)
    except (ImportError, ValueError) as exc:
        raise SourceAdapterError("artifact_contract_invalid", str(exc)) from exc
    finally:
        if inserted:
            sys.path.remove(str(contracts_src))


def _artifact_id(report: Mapping[str, Any], html_hash: str) -> str:
    identity = report["research_identity"]
    material = ":".join(str(identity[field]) for field in ("market", "company_id", "filing_id", "parse_run_id"))
    generated_at = str(report.get("generated_at") or "")
    return f"analysis_{hashlib.sha256(f'{material}:{html_hash}:{generated_at}'.encode()).hexdigest()[:24]}"


def _evidence_id(evidence: Mapping[str, Any]) -> str:
    explicit = str(evidence.get("evidence_id") or "").strip()
    if explicit:
        return explicit
    material = json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str)
    return "evidence_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _fact_evidence_ids(fact: Mapping[str, Any]) -> list[str]:
    refs = fact.get("evidence_refs") if isinstance(fact.get("evidence_refs"), list) else []
    ids = [_evidence_id(item) for item in refs if isinstance(item, Mapping)]
    ids.extend(str(item) for item in fact.get("evidence_ids", ()) if item)
    return list(dict.fromkeys(ids))


def _evidence_has_locator(evidence: Mapping[str, Any]) -> bool:
    return any(
        evidence.get(key) not in (None, "")
        for key in (
            "source_url",
            "pdf_page",
            "table_id",
            "section_id",
            "html_anchor",
            "xpath",
            "xbrl_fact_id",
            "xbrl_concept",
            "md_line",
            "chunk_index",
        )
    ) or bool(evidence.get("locator"))


def _presentation_evidence(report: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return the bounded evidence set used by human-readable report surfaces.

    The JSON companion remains the complete audit payload. HTML and Markdown
    only need the locators that support published core claims; embedding every
    raw SEC/XBRL fact makes the report slower and turns provenance into noise.
    """

    evidence_by_id = {
        _evidence_id(item): item
        for item in report.get("evidence_refs") or ()
        if isinstance(item, Mapping)
    }
    required_ids: list[str] = []
    for claim in report.get("claims") or ():
        if not isinstance(claim, Mapping):
            continue
        required_ids.extend(str(item) for item in claim.get("evidence_ids") or () if item)
    ordered_ids = list(dict.fromkeys(required_ids))
    return [
        evidence_by_id[evidence_id]
        for evidence_id in ordered_ids[:MAX_PRESENTATION_EVIDENCE_ITEMS]
        if evidence_id in evidence_by_id
    ]


def _html_has_bare_evidence_id_link(html_text: str) -> bool:
    for match in re.finditer(
        r'<a\b[^>]*href=["\']#evidence-[^"\']+["\'][^>]*>(.*?)</a\s*>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        label = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        if re.fullmatch(r"(?:evidence[-_:])?[0-9a-f]{24,64}|ev[-_:][\w.-]+", label, re.IGNORECASE):
            return True
    return False


def _ordered_evidence(
    report: Mapping[str, Any],
    *,
    limit: int | None = None,
) -> list[Mapping[str, Any]]:
    evidence_by_id: dict[str, Mapping[str, Any]] = {}
    for item in report.get("evidence_refs") or ():
        if isinstance(item, Mapping):
            evidence_by_id.setdefault(_evidence_id(item), item)
    evidence = list(evidence_by_id.values())
    referenced = {
        evidence_id
        for fact in report.get("facts") or ()
        if isinstance(fact, Mapping)
        for evidence_id in _fact_evidence_ids(fact)
    }
    evidence.sort(key=lambda item: (_evidence_id(item) not in referenced, _evidence_id(item)))
    return evidence if limit is None else evidence[:limit]


def _evidence_href(evidence: Mapping[str, Any]) -> str:
    source_url = str(evidence.get("source_url") or "").strip()
    anchor = str(evidence.get("html_anchor") or "").strip()
    if source_url.startswith(("https://", "http://", "/api/")):
        if anchor and "#" not in source_url:
            return f"{source_url}#{anchor}"
        return source_url
    task_id = str(evidence.get("pdf_task_id") or "").strip()
    pdf_page = evidence.get("pdf_page")
    if task_id and isinstance(pdf_page, int):
        return f"/api/pdf_page/{task_id}/{pdf_page}"
    target = str(evidence.get("target") or "").strip()
    return target if target.startswith(("https://", "http://", "/api/")) else ""


def _evidence_locator_label(evidence: Mapping[str, Any]) -> str:
    kind = str(evidence.get("kind") or "source")
    if evidence.get("xbrl_concept"):
        context = str(evidence.get("xbrl_context") or "")
        return f"XBRL 事实{f' · 上下文 {context}' if context else ''}"
    if evidence.get("section_id") or evidence.get("html_anchor"):
        return f"报告章节 · {evidence.get('section_id') or evidence.get('html_anchor')}"
    if evidence.get("pdf_page") is not None:
        table = f" · 表 {evidence['table_id']}" if evidence.get("table_id") else ""
        return f"第 {evidence['pdf_page']} 页{table}"
    if evidence.get("md_line") is not None:
        return f"报告文本第 {evidence['md_line']} 行"
    if evidence.get("chunk_index") is not None:
        return f"全文片段 {evidence['chunk_index']}"
    return {"xbrl_fact": "XBRL 事实", "pdf_table": "报表表格", "section": "报告章节"}.get(
        kind,
        "源报告定位",
    )


def _evidence_group_label(evidence: Mapping[str, Any]) -> str:
    role = str(evidence.get("section_role") or "").strip().lower()
    if role:
        return {
            "business": "业务概览",
            "mda": "管理层讨论与分析",
            "segments": "分部信息",
            "risk_factors": "风险因素",
            "market_risk": "市场风险",
            "controls": "控制与治理",
            "notes": "财务报表附注",
            "financial_statements": "财务报表",
        }.get(role, "其他报告章节")
    kind = str(evidence.get("kind") or evidence.get("source_type") or "").lower()
    if "xbrl" in kind:
        return "结构化财务事实"
    if "table" in kind or evidence.get("pdf_page") is not None:
        return "报表与表格"
    return "其他可回溯材料"


def _group_evidence(
    evidence: Sequence[Mapping[str, Any]],
) -> list[tuple[str, list[Mapping[str, Any]]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for item in evidence:
        grouped.setdefault(_evidence_group_label(item), []).append(item)
    return [(label, grouped[label]) for label in sorted(grouped)]


def _evidence_excerpt(evidence: Mapping[str, Any]) -> str:
    excerpt = " ".join(str(evidence.get("quote") or evidence.get("quote_text") or "").split())
    return excerpt[:237].rstrip() + "..." if len(excerpt) > 240 else excerpt


def _evidence_semantic_label(evidence: Mapping[str, Any]) -> str:
    target = str(evidence.get("metric_key") or evidence.get("target") or evidence.get("canonical_name") or "").strip()
    normalized_target = target.lower().replace("-", "_").replace(" ", "_")
    subject = METRIC_LABELS.get(normalized_target)
    if not subject:
        role_label = _evidence_group_label(evidence)
        subject = role_label if role_label != "其他可回溯材料" else "报告原文"
    locator = _evidence_locator_label(evidence)
    return f"{subject}（{locator}）"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validate_staged_publication(
    staged_paths: Mapping[str, Path],
    *,
    expected_hashes: Mapping[str, str],
    sidecar: Mapping[str, Any],
) -> dict[str, Any]:
    failures: list[str] = []
    for key in ("md", "json", "html", "sidecar"):
        path = staged_paths[key]
        if not path.is_file() or path.is_symlink():
            failures.append(f"staged_file_invalid:{key}")
    for key in ("md", "json", "html"):
        path = staged_paths[key]
        if path.is_file() and _sha256(path.read_bytes()) != expected_hashes[key]:
            failures.append(f"staged_hash_mismatch:{key}")
    try:
        staged_sidecar = json.loads(staged_paths["sidecar"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        staged_sidecar = {}
        failures.append("staged_sidecar_invalid")
    if staged_sidecar != dict(sidecar):
        failures.append("staged_sidecar_mismatch")
    if staged_sidecar.get("content_hash") != expected_hashes.get("html"):
        failures.append("staged_html_hash_unbound")
    return {
        "ok": not failures,
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "content_hashes": dict(expected_hashes),
        "sidecar_commit_marker": staged_paths["sidecar"].name,
    }


def _publish_staged_files(
    staged_paths: Mapping[str, Path],
    final_paths: Mapping[str, Path],
    *,
    allow_overwrite: bool,
) -> None:
    # HTML is the final readiness marker. If the process dies earlier, the
    # resolver ignores a sidecar whose declared HTML does not yet exist.
    order = ("sidecar", "md", "json", "html")
    backups: dict[str, Path] = {}
    published: list[str] = []
    for key in order:
        if final_paths[key].is_symlink():
            raise SourceAdapterError("unsafe_path_rejected", "formal artifact destination is a symbolic link")
        if final_paths[key].exists() and not allow_overwrite:
            raise SourceAdapterError("artifact_exists", "formal analysis artifact already exists")
    try:
        for key in order:
            final_path = final_paths[key]
            if final_path.exists():
                backup = staged_paths[key].parent / f".{final_path.name}.backup"
                if backup.exists():
                    backup.unlink()
                final_path.replace(backup)
                backups[key] = backup
            staged_paths[key].replace(final_path)
            published.append(key)
    except OSError as exc:
        for key in reversed(published):
            try:
                final_paths[key].unlink(missing_ok=True)
            except OSError:
                pass
        for key, backup in backups.items():
            if backup.exists():
                backup.replace(final_paths[key])
        raise SourceAdapterError(
            "artifact_publish_failed",
            "formal analysis artifacts could not be published atomically",
        ) from exc
    for backup in backups.values():
        backup.unlink(missing_ok=True)


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
