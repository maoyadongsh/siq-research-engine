"""Build and validate market-neutral research packs for formal analysis."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from analysis_input_bundle import validate_analysis_input_bundle
from input_adapters import SourceAdapterError


PACK_SCHEMA_VERSION = "siq_analysis_research_pack_v2"
MANIFEST_SCHEMA_VERSION = "siq_analysis_research_pack_manifest_v2"
VALIDATION_SCHEMA_VERSION = "siq_analysis_research_pack_validation_v2"
MERGE_SCHEMA_VERSION = "siq_analysis_research_pack_merge_v2"
REQUIRED_AGENT_IDS = (
    "evidence_curator",
    "financial_modeler",
    "business_strategy_researcher",
    "industry_peer_researcher",
    "governance_risk_researcher",
)
CORE_METRIC_KEYS = {
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
SECTION_IDS = (
    "executive_summary",
    "business_overview",
    "revenue_quality",
    "profitability",
    "balance_sheet",
    "cash_flow",
    "capital_allocation",
    "segments",
    "risk_factors",
    "controls",
    "accounting_quality",
    "valuation_boundary",
    "tracking",
    "traceability",
)


def build_formal_research_packs(
    bundle: Mapping[str, Any],
    *,
    work_dir: Path,
) -> dict[str, Any]:
    """Create the shared five-pack checkpoint after source-family adaptation."""

    payload = validate_analysis_input_bundle(bundle)
    resolved_work_dir = work_dir.resolve()
    analysis_dir = Path(str((payload.get("server_paths") or {}).get("analysis_dir") or "")).resolve()
    try:
        relative_work_dir = resolved_work_dir.relative_to(analysis_dir)
    except ValueError as exc:
        raise SourceAdapterError("unsafe_path_rejected", "research-pack work directory escapes analysis/") from exc
    if not relative_work_dir.parts or relative_work_dir.parts[0] != ".work":
        raise SourceAdapterError("unsafe_path_rejected", "research packs must stay inside analysis/.work/")

    identity = dict(payload["research_identity"])
    source_report = dict(payload["source_report"])
    report_id = str(source_report.get("report_id") or "")
    adapter = dict(payload["adapter"])
    facts = [dict(item) for item in payload.get("normalized_facts", ()) if isinstance(item, Mapping)]
    evidence = [dict(item) for item in payload.get("evidence_refs", ()) if isinstance(item, Mapping)]
    evidence_by_id = {_evidence_id(item): item for item in evidence}
    failures = _input_failures(
        identity=identity,
        report_id=report_id,
        facts=facts,
        evidence=evidence,
        evidence_by_id=evidence_by_id,
    )

    packs = _build_packs(
        identity=identity,
        report_id=report_id,
        adapter=adapter,
        facts=facts,
        evidence=evidence,
        evidence_by_id=evidence_by_id,
        capabilities=dict(payload.get("capabilities") or {}),
        quality=dict(payload.get("quality") or {}),
    )
    failures.extend(_pack_failures(packs, identity=identity, report_id=report_id, evidence_by_id=evidence_by_id))
    merge_manifest = _build_merge_manifest(
        packs,
        identity=identity,
        report_id=report_id,
        adapter=adapter,
    )
    failures.extend(_merge_failures(merge_manifest))
    validation = {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "ok": not failures,
        "status": "pass" if not failures else "fail",
        "validated_at": datetime.now(UTC).isoformat(),
        "research_identity": identity,
        "source_report_id": report_id,
        "adapter": adapter,
        "pack_count": len(packs),
        "fact_count": len(facts),
        "evidence_count": len(evidence),
        "failures": list(dict.fromkeys(failures)),
    }

    packs_dir = resolved_work_dir / "research_packs"
    packs_dir.mkdir(parents=True, exist_ok=True)
    pack_paths: dict[str, Path] = {}
    for pack in packs:
        path = packs_dir / f"{pack['agent_id']}.json"
        _atomic_write_json(path, pack)
        pack_paths[str(pack["agent_id"])] = path
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "research_identity": identity,
        "source_report_id": report_id,
        "adapter": adapter,
        "quality": dict(payload.get("quality") or {}),
        "required_agent_ids": list(REQUIRED_AGENT_IDS),
        "packs": [
            {
                "agent_id": agent_id,
                "file": path.name,
                "content_hash": _sha256(path.read_bytes()),
            }
            for agent_id, path in sorted(pack_paths.items())
        ],
    }
    manifest_path = resolved_work_dir / "research_pack_manifest.json"
    merge_path = resolved_work_dir / "research_pack_merge_manifest.json"
    validation_path = resolved_work_dir / "research_pack_validation.json"
    _atomic_write_json(manifest_path, manifest)
    _atomic_write_json(merge_path, merge_manifest)
    _atomic_write_json(validation_path, validation)
    if failures:
        raise SourceAdapterError(
            "research_pack_validation_failed",
            "formal research packs failed their identity or evidence gate",
            details={"failures": validation["failures"], "validation_path": str(validation_path)},
        )
    return {
        "ok": True,
        "packs": packs,
        "manifest": manifest,
        "merge_manifest": merge_manifest,
        "validation": validation,
        "paths": {
            "research_packs_dir": str(packs_dir),
            "research_pack_manifest": str(manifest_path),
            "research_pack_merge_manifest": str(merge_path),
            "research_pack_validation": str(validation_path),
        },
    }


def _build_packs(
    *,
    identity: Mapping[str, Any],
    report_id: str,
    adapter: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    capabilities: Mapping[str, Any],
    quality: Mapping[str, Any],
) -> list[dict[str, Any]]:
    evidence_ids = [_evidence_id(item) for item in evidence]
    role_evidence: dict[str, list[str]] = {}
    for item in evidence:
        role = str(item.get("section_role") or "").strip()
        if role:
            role_evidence.setdefault(role, []).append(_evidence_id(item))

    financial_findings: list[dict[str, Any]] = []
    fact_ids: list[str] = []
    for fact in facts:
        fact_id = _fact_id(fact)
        fact_ids.append(fact_id)
        metric_key = str(fact.get("metric_key") or "")
        if metric_key not in CORE_METRIC_KEYS or _numeric_value(fact) is None:
            continue
        refs = _fact_evidence_ids(fact)
        if refs:
            financial_findings.append(
                {
                    "finding_id": f"finding_{fact_id}",
                    "fact_id": fact_id,
                    "fact_status": "verified_fact",
                    "section_ids": _financial_sections(metric_key),
                    "metric_key": metric_key,
                    "period_end": fact.get("period_end"),
                    "accounting_basis": fact.get("accounting_basis"),
                    "evidence_ids": refs,
                }
            )

    business_ids = _role_ids(role_evidence, "business", "mda", "segments")
    governance_ids = _role_ids(
        role_evidence,
        "risk_factors",
        "market_risk",
        "controls",
        "notes",
        "financial_statements",
    )
    limits = [str(item) for item in quality.get("degraded_reasons") or ()]
    warnings = [str(item) for item in quality.get("warnings") or ()]
    common = {
        "schema_version": PACK_SCHEMA_VERSION,
        "research_identity": dict(identity),
        "source_report_id": report_id,
        "adapter": dict(adapter),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    return [
        {
            **common,
            "agent_id": "evidence_curator",
            "coverage": {"section_ids": list(SECTION_IDS), "source_scope": [adapter.get("source_family")], "known_limits": limits},
            "finding_records": [],
            "fact_ids": [],
            "evidence_ids": evidence_ids,
            "missing_inputs": [] if evidence_ids else ["evidence_unavailable"],
            "review_required": not bool(evidence_ids),
        },
        {
            **common,
            "agent_id": "financial_modeler",
            "coverage": {
                "section_ids": ["executive_summary", "revenue_quality", "profitability", "balance_sheet", "cash_flow", "capital_allocation", "accounting_quality"],
                "source_scope": ["normalized_facts"],
                "known_limits": limits,
            },
            "finding_records": financial_findings,
            "fact_ids": fact_ids,
            "evidence_ids": list(dict.fromkeys(item for finding in financial_findings for item in finding["evidence_ids"])),
            "missing_inputs": [] if facts else ["structured_metrics_unavailable"],
            "review_required": not bool(facts),
        },
        {
            **common,
            "agent_id": "business_strategy_researcher",
            "coverage": {"section_ids": ["business_overview", "profitability", "segments"], "source_scope": ["report_sections"], "known_limits": limits},
            "finding_records": _section_findings(business_ids, role_evidence, ("business", "mda", "segments")),
            "fact_ids": [],
            "evidence_ids": business_ids,
            "missing_inputs": [] if business_ids else ["business_section_evidence_unavailable"],
            "review_required": not bool(business_ids),
        },
        {
            **common,
            "agent_id": "industry_peer_researcher",
            "coverage": {"section_ids": ["valuation_boundary"], "source_scope": [], "known_limits": [*limits, "cross_market_peer_comparison_not_enabled"]},
            "finding_records": [],
            "fact_ids": [],
            "evidence_ids": [],
            "missing_inputs": [
                reason
                for available, reason in (
                    (capabilities.get("peer_metrics"), "peer_metrics_unavailable"),
                    (capabilities.get("market_snapshot"), "market_snapshot_unavailable"),
                )
                if not available
            ],
            "review_required": False,
        },
        {
            **common,
            "agent_id": "governance_risk_researcher",
            "coverage": {"section_ids": ["risk_factors", "controls", "accounting_quality", "tracking"], "source_scope": ["report_sections", "financial_checks"], "known_limits": [*limits, *warnings]},
            "finding_records": _section_findings(
                governance_ids,
                role_evidence,
                ("risk_factors", "market_risk", "controls", "notes", "financial_statements"),
            ),
            "fact_ids": [],
            "evidence_ids": governance_ids,
            "missing_inputs": [] if governance_ids else ["risk_and_governance_evidence_unavailable"],
            "review_required": not bool(governance_ids),
        },
    ]


def _input_failures(
    *,
    identity: Mapping[str, Any],
    report_id: str,
    facts: Sequence[Mapping[str, Any]],
    evidence: Sequence[Mapping[str, Any]],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    failures: list[str] = []
    for item in evidence:
        evidence_id = _evidence_id(item)
        if dict(item.get("research_identity") or {}) != dict(identity):
            failures.append(f"evidence_identity_mismatch:{evidence_id}")
        if str(item.get("report_id") or "") != report_id:
            failures.append(f"evidence_report_mismatch:{evidence_id}")
        if not _evidence_has_locator(item):
            failures.append(f"evidence_locator_missing:{evidence_id}")
    for fact in facts:
        fact_id = _fact_id(fact)
        if dict(fact.get("research_identity") or {}) != dict(identity):
            failures.append(f"fact_identity_mismatch:{fact_id}")
        if str(fact.get("metric_key") or "") in CORE_METRIC_KEYS and _numeric_value(fact) is not None:
            refs = _fact_evidence_ids(fact)
            if not refs:
                failures.append(f"core_fact_evidence_missing:{fact_id}")
            for evidence_id in refs:
                if evidence_id not in evidence_by_id:
                    failures.append(f"core_fact_evidence_unknown:{fact_id}:{evidence_id}")
    return failures


def _pack_failures(
    packs: Sequence[Mapping[str, Any]],
    *,
    identity: Mapping[str, Any],
    report_id: str,
    evidence_by_id: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    failures: list[str] = []
    seen = {str(pack.get("agent_id") or "") for pack in packs}
    for missing in sorted(set(REQUIRED_AGENT_IDS) - seen):
        failures.append(f"research_pack_missing:{missing}")
    for pack in packs:
        agent_id = str(pack.get("agent_id") or "unknown")
        if pack.get("schema_version") != PACK_SCHEMA_VERSION:
            failures.append(f"research_pack_schema_invalid:{agent_id}")
        if dict(pack.get("research_identity") or {}) != dict(identity):
            failures.append(f"research_pack_identity_mismatch:{agent_id}")
        if str(pack.get("source_report_id") or "") != report_id:
            failures.append(f"research_pack_report_mismatch:{agent_id}")
        for evidence_id in pack.get("evidence_ids") or ():
            if str(evidence_id) not in evidence_by_id:
                failures.append(f"research_pack_evidence_unknown:{agent_id}:{evidence_id}")
        for finding in pack.get("finding_records") or ():
            if not isinstance(finding, Mapping):
                failures.append(f"research_pack_finding_invalid:{agent_id}")
                continue
            if finding.get("fact_status") == "verified_fact" and not finding.get("evidence_ids"):
                failures.append(f"research_pack_verified_fact_unbound:{agent_id}")
    return failures


def _build_merge_manifest(
    packs: Sequence[Mapping[str, Any]],
    *,
    identity: Mapping[str, Any],
    report_id: str,
    adapter: Mapping[str, Any],
) -> dict[str, Any]:
    sections: dict[str, dict[str, Any]] = {
        section_id: {
            "agent_ids": [],
            "finding_ids": [],
            "fact_ids": [],
            "evidence_ids": [],
        }
        for section_id in SECTION_IDS
    }
    for pack in packs:
        agent_id = str(pack.get("agent_id") or "")
        coverage = pack.get("coverage") if isinstance(pack.get("coverage"), Mapping) else {}
        for section_id in coverage.get("section_ids") or ():
            if str(section_id) in sections:
                sections[str(section_id)]["agent_ids"].append(agent_id)
        for finding in pack.get("finding_records") or ():
            if not isinstance(finding, Mapping):
                continue
            for section_id in finding.get("section_ids") or ():
                section = sections.get(str(section_id))
                if section is None:
                    continue
                section["agent_ids"].append(agent_id)
                if finding.get("finding_id"):
                    section["finding_ids"].append(str(finding["finding_id"]))
                if finding.get("fact_id"):
                    section["fact_ids"].append(str(finding["fact_id"]))
                section["evidence_ids"].extend(str(item) for item in finding.get("evidence_ids") or () if item)
        if agent_id == "evidence_curator":
            sections["traceability"]["evidence_ids"].extend(
                str(item) for item in pack.get("evidence_ids") or () if item
            )
    for section in sections.values():
        for key in ("agent_ids", "finding_ids", "fact_ids", "evidence_ids"):
            section[key] = list(dict.fromkeys(section[key]))
    return {
        "schema_version": MERGE_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "research_identity": dict(identity),
        "source_report_id": report_id,
        "adapter": dict(adapter),
        "sections": sections,
    }


def _merge_failures(merge_manifest: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    sections = merge_manifest.get("sections") if isinstance(merge_manifest.get("sections"), Mapping) else {}
    for section_id in SECTION_IDS:
        if section_id not in sections:
            failures.append(f"research_pack_merge_section_missing:{section_id}")
    required_provenance = {
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
    }
    for section_id, agent_id in required_provenance.items():
        section = sections.get(section_id) if isinstance(sections.get(section_id), Mapping) else {}
        if agent_id not in (section.get("agent_ids") or ()):
            failures.append(f"research_pack_merge_provenance_missing:{section_id}:{agent_id}")
    return failures


def _section_findings(
    evidence_ids: Sequence[str],
    by_role: Mapping[str, Sequence[str]],
    roles: Sequence[str],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    allowed = set(evidence_ids)
    for role in roles:
        refs = [item for item in by_role.get(role, ()) if item in allowed]
        if not refs:
            continue
        output.append(
            {
                "finding_id": f"section_{role}",
                "fact_status": "verified_fact",
                "section_ids": _report_sections_for_role(role),
                "section_role": role,
                "evidence_ids": list(dict.fromkeys(refs)),
            }
        )
    return output


def _report_sections_for_role(role: str) -> list[str]:
    return {
        "business": ["business_overview"],
        "mda": ["profitability", "revenue_quality"],
        "segments": ["segments"],
        "risk_factors": ["risk_factors"],
        "market_risk": ["risk_factors", "tracking"],
        "controls": ["controls"],
        "notes": ["accounting_quality"],
        "financial_statements": ["accounting_quality"],
    }.get(role, ["traceability"])


def _financial_sections(metric_key: str) -> list[str]:
    if metric_key in {"net_interest_income", "net_interest_margin", "insurance_revenue", "insurance_service_result"}:
        return ["executive_summary", "revenue_quality", "profitability"]
    if metric_key in {
        "capital_adequacy_ratio",
        "core_tier_1_capital_adequacy_ratio",
        "tier_1_capital_ratio",
        "solvency_ratio",
    }:
        return ["capital_allocation", "tracking"]
    if metric_key in {"revenue", "operating_revenue", "total_revenue"}:
        return ["executive_summary", "revenue_quality"]
    if metric_key in {"net_income", "net_profit", "parent_net_profit", "net_profit_parent"}:
        return ["executive_summary", "profitability"]
    if metric_key in {"operating_cash_flow", "operating_cash_flow_net", "net_operating_cash_flow"}:
        return ["executive_summary", "cash_flow"]
    return ["balance_sheet"]


def _role_ids(by_role: Mapping[str, Sequence[str]], *roles: str) -> list[str]:
    return list(dict.fromkeys(item for role in roles for item in by_role.get(role, ())))


def _fact_evidence_ids(fact: Mapping[str, Any]) -> list[str]:
    refs = fact.get("evidence_refs") if isinstance(fact.get("evidence_refs"), list) else []
    return list(dict.fromkeys(_evidence_id(item) for item in refs if isinstance(item, Mapping)))


def _fact_id(fact: Mapping[str, Any]) -> str:
    explicit = str(fact.get("fact_id") or "").strip()
    if explicit:
        return explicit
    return "fact_" + hashlib.sha256(
        json.dumps(fact, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]


def _evidence_id(evidence: Mapping[str, Any]) -> str:
    explicit = str(evidence.get("evidence_id") or "").strip()
    if explicit:
        return explicit
    return "evidence_" + hashlib.sha256(
        json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]


def _numeric_value(fact: Mapping[str, Any]) -> float | None:
    value = fact.get("normalized_value") if fact.get("normalized_value") is not None else fact.get("value")
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
    )


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "PACK_SCHEMA_VERSION",
    "VALIDATION_SCHEMA_VERSION",
    "build_formal_research_packs",
]
