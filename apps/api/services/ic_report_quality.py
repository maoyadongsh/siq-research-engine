"""Deterministic report and factcheck gates for formal IC publication."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from services.ic_contract_validation import ICContractValidationError, validate_schema
from services.ic_report_contracts import (
    report_evidence_ids,
    validate_expert_report,
    validate_r4_decision,
)

IC_REPORT_FACTCHECK_SCHEMA = "siq_ic_report_factcheck_v1"
IC_REPORT_QUALITY_SCHEMA = "siq_ic_report_quality_v1"
IC_REPORT_REPAIR_REVISION_SCHEMA = "siq_ic_report_repair_revision_v1"

PLACEHOLDER_PATTERNS = (
    re.compile(r"\b(?:TODO|TBD|FIXME)\b", re.IGNORECASE),
    re.compile(r"\{\{[^}]+\}\}"),
    re.compile(r"<[^>]*(?:placeholder|待填|填写)[^>]*>", re.IGNORECASE),
    re.compile(r"请参见(?:其他|上述|相关)?文件"),
)
INTERNAL_PATH_PATTERNS = (
    re.compile(r"/(?:home|Users|var/lib|srv)/[^\s`<]+"),
    re.compile(r"[A-Za-z]:\\[^\s`<]+"),
    re.compile(r"(?:system prompt|gateway_url|HERMES_GATEWAY|内部提示词)", re.IGNORECASE),
)
FINANCIAL_TOPIC_RE = re.compile(
    r"(?:revenue|profit|cash|valuation|financial|margin|ebitda|收入|利润|现金流|估值|毛利|财务)",
    re.IGNORECASE,
)
PRECISE_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])[-+]?\d+(?:\.\d+)?(?:%|亿元|万元|元|倍)?")
MONETARY_UNIT_RE = re.compile(
    r"(?:cny|rmb|usd|eur|jpy|gbp|yuan|dollar|million|billion|thousand|百万元|亿元|万元|元)",
    re.IGNORECASE,
)
BACKGROUND_SOURCE_TYPES = {"background_knowledge", "milvus_background", "knowledge_base"}
REQUIRED_EXPERT_AGENT_IDS = (
    "siq_ic_strategist",
    "siq_ic_sector_expert",
    "siq_ic_finance_auditor",
    "siq_ic_legal_scanner",
    "siq_ic_risk_controller",
)


FACTCHECK_SERVER_MANAGED_FIELDS = (
    "report_id",
    "report_revision",
    "checked_at",
    "evidence_snapshot_hash",
)
FACTCHECK_MAX_FINDINGS_PER_CATEGORY = 20
FACTCHECK_MAX_EVIDENCE_IDS_PER_FINDING = 50


def _finding_array_schema() -> dict[str, Any]:
    short_text = {"type": "string", "minLength": 1, "maxLength": 1200}
    return {
        "type": "array",
        "maxItems": FACTCHECK_MAX_FINDINGS_PER_CATEGORY,
        "items": {
            "oneOf": [
                short_text,
                {
                    "type": "object",
                    "minProperties": 1,
                    "maxProperties": 10,
                    "additionalProperties": False,
                    "properties": {
                        "id": short_text,
                        "check_id": short_text,
                        "claim_id": short_text,
                        "status": {
                            "enum": [
                                "pass",
                                "warn",
                                "fail",
                                "verified",
                                "unsupported",
                                "missing",
                            ]
                        },
                        "severity": {
                            "enum": ["info", "low", "medium", "high", "critical"]
                        },
                        "message": short_text,
                        "finding": short_text,
                        "action": short_text,
                        "repair": short_text,
                        "rationale": short_text,
                        "evidence_ids": {
                            "type": "array",
                            "maxItems": FACTCHECK_MAX_EVIDENCE_IDS_PER_FINDING,
                            "items": {"type": "string", "maxLength": 128},
                        },
                    },
                },
            ]
        },
    }


FACTCHECK_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_REPORT_FACTCHECK_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "status",
        "claim_checks",
        "numeric_checks",
        "citation_checks",
        "contradictions",
        "unsupported_claims",
        "required_repairs",
    ],
    "properties": {
        "schema_version": {"const": IC_REPORT_FACTCHECK_SCHEMA},
        "status": {"enum": ["pass", "warn", "fail"]},
        "claim_checks": _finding_array_schema(),
        "numeric_checks": _finding_array_schema(),
        "citation_checks": _finding_array_schema(),
        "contradictions": _finding_array_schema(),
        "unsupported_claims": _finding_array_schema(),
        "required_repairs": _finding_array_schema(),
        "report_id": {"type": "string", "pattern": r"^ICRPT-[A-Z0-9][A-Z0-9-]{7,95}$"},
        "report_revision": {"type": "integer", "minimum": 1},
        "checked_at": {"type": "string", "format": "date-time"},
        "evidence_snapshot_hash": {"type": "string", "pattern": r"^[a-fA-F0-9]{64}$"},
    },
}


def factcheck_authoring_schema() -> dict[str, Any]:
    schema = deepcopy(FACTCHECK_JSON_SCHEMA)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for field in FACTCHECK_SERVER_MANAGED_FIELDS:
            properties.pop(field, None)
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [
            field for field in required if field not in FACTCHECK_SERVER_MANAGED_FIELDS
        ]
    schema["$id"] = (
        f"https://siq.local/schemas/ic/model-authoring/{IC_REPORT_FACTCHECK_SCHEMA}"
    )
    schema["x-persisted-final-contract"] = IC_REPORT_FACTCHECK_SCHEMA
    schema["x-projection"] = "server_managed_fields_omitted"
    return schema


def _check(check_id: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "message": message,
        **{key: value for key, value in details.items() if value not in (None, "", [], {})},
    }


def _overall(checks: Sequence[Mapping[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in checks}
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def _known_evidence_map(
    known_evidence: Mapping[str, Mapping[str, Any]] | Sequence[str] | set[str] | None,
) -> dict[str, Mapping[str, Any] | None]:
    if known_evidence is None:
        return {}
    if isinstance(known_evidence, Mapping):
        return {str(key): value for key, value in known_evidence.items()}
    return {str(item): None for item in known_evidence}


def _all_reports(
    decision: Mapping[str, Any],
    expert_reports: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | None,
) -> list[Mapping[str, Any]]:
    reports = [decision]
    if isinstance(expert_reports, Mapping):
        reports.extend(item for item in expert_reports.values() if isinstance(item, Mapping))
    elif isinstance(expert_reports, Sequence) and not isinstance(expert_reports, (str, bytes)):
        reports.extend(item for item in expert_reports if isinstance(item, Mapping))
    return reports


def _all_claims(reports: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        claim
        for report in reports
        for claim in report.get("claims", [])
        if isinstance(claim, Mapping)
    ]


def _is_decision_relevant_numeric_claim(claim: Mapping[str, Any]) -> bool:
    if claim.get("decision_impact") not in {"critical", "material"}:
        return False
    if claim.get("value") not in (None, ""):
        return True
    text = f"{claim.get('topic') or ''} {claim.get('conclusion') or ''}"
    return bool(FINANCIAL_TOPIC_RE.search(text) and PRECISE_NUMBER_RE.search(text))


def _numeric_trace_missing_fields(claim: Mapping[str, Any]) -> list[str]:
    if not _is_decision_relevant_numeric_claim(claim):
        return []
    status = str(claim.get("status") or "").lower()
    if status == "missing":
        return []

    missing = [field for field in ("period", "unit") if claim.get(field) in (None, "")]
    unit = str(claim.get("unit") or "")
    if MONETARY_UNIT_RE.search(unit) and claim.get("currency") in (None, ""):
        missing.append("currency")
    if not claim.get("evidence_ids"):
        missing.append("evidence_ids")
    if status in {"derived", "assumed"} and not claim.get("calculation_trace_ids"):
        missing.append("calculation_trace_ids")
    return missing


def _unresolved_veto(veto: Any) -> bool:
    if isinstance(veto, str):
        return bool(veto.strip())
    if not isinstance(veto, Mapping):
        return False
    status = str(veto.get("status") or "open").strip().lower()
    return status not in {"closed", "resolved", "overridden", "accepted"}


def _critical_unsupported_claim_ids(factcheck: Mapping[str, Any], claims: Sequence[Mapping[str, Any]]) -> list[str]:
    critical_ids = {
        str(claim.get("claim_id"))
        for claim in claims
        if claim.get("decision_impact") == "critical"
    }
    blocked: set[str] = set()
    for item in factcheck.get("unsupported_claims", []):
        if isinstance(item, str):
            if item in critical_ids:
                blocked.add(item)
            continue
        if not isinstance(item, Mapping):
            continue
        claim_id = str(item.get("claim_id") or "")
        severity = str(item.get("severity") or "").lower()
        if severity == "critical" or claim_id in critical_ids:
            blocked.add(claim_id or "unidentified_critical_claim")
    return sorted(blocked)


def validate_factcheck_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    factcheck = validate_schema(payload, FACTCHECK_JSON_SCHEMA, contract=IC_REPORT_FACTCHECK_SCHEMA)
    if factcheck["status"] == "pass" and (
        factcheck["unsupported_claims"] or factcheck["required_repairs"] or factcheck["contradictions"]
    ):
        raise ICContractValidationError(
            IC_REPORT_FACTCHECK_SCHEMA,
            ["factcheck_pass_contains_unresolved_findings"],
        )
    return factcheck


def validate_factcheck_authoring_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    return validate_schema(
        payload,
        factcheck_authoring_schema(),
        contract=f"{IC_REPORT_FACTCHECK_SCHEMA}#model-authoring-payload",
    )


def build_factcheck_input(
    decision: Mapping[str, Any],
    *,
    markdown: str,
    evidence: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": "siq_ic_report_factcheck_input_v1",
        "report_id": decision.get("report_id"),
        "report_revision": decision.get("revision", 1),
        "deal_id": decision.get("deal_id"),
        "evidence_snapshot_hash": decision.get("evidence_snapshot_hash"),
        "decision": decision.get("decision"),
        "claims": deepcopy(list(decision.get("claims") or [])),
        "background_knowledge_refs": deepcopy(list(decision.get("background_knowledge_refs") or [])),
        "markdown": str(markdown),
        "evidence": deepcopy(dict(evidence or {})),
    }
    digest_source = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    payload["input_digest"] = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
    return payload


def evaluate_report_quality(
    decision: Mapping[str, Any],
    *,
    expert_reports: Sequence[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | None = None,
    known_evidence: Mapping[str, Mapping[str, Any]] | Sequence[str] | set[str] | None = None,
    expected_deal_id: str | None = None,
    expected_snapshot_hash: str | None = None,
    disputes: Sequence[Mapping[str, Any]] | None = None,
    r3_plan: Mapping[str, Any] | None = None,
    rendered_markdown: str = "",
    rendered_html: str = "",
    required_section_titles: Sequence[str] | None = None,
    factcheck: Mapping[str, Any] | None = None,
    required_expert_agent_ids: Sequence[str] = REQUIRED_EXPERT_AGENT_IDS,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    evidence_map = _known_evidence_map(known_evidence)
    validation_known_evidence = known_evidence if known_evidence is not None else None

    try:
        validate_r4_decision(
            decision,
            expected_deal_id=expected_deal_id,
            expected_snapshot_hash=expected_snapshot_hash,
            known_evidence=validation_known_evidence,
        )
    except (ICContractValidationError, ValueError) as exc:
        checks.append(
            _check(
                "schema.r4",
                "fail",
                "R4 structured decision is invalid.",
                errors=getattr(exc, "errors", [str(exc)]),
            )
        )
    else:
        checks.append(_check("schema.r4", "pass", "R4 structured decision contract is valid."))

    normalized_expert_reports = _all_reports({}, expert_reports)[1:]
    report_errors: list[str] = []
    present_agents = {str(report.get("agent_id") or "") for report in normalized_expert_reports}
    missing_agents = sorted(set(required_expert_agent_ids) - present_agents)
    if missing_agents:
        report_errors.append("missing_expert_reports:" + ",".join(missing_agents))
    for report in normalized_expert_reports:
        try:
            validate_expert_report(
                report,
                expected_deal_id=expected_deal_id,
                expected_snapshot_hash=expected_snapshot_hash,
                known_evidence=validation_known_evidence,
            )
        except (ICContractValidationError, ValueError) as exc:
            report_errors.extend(getattr(exc, "errors", [str(exc)]))
    checks.append(
        _check(
            "schema.expert_reports",
            "fail" if report_errors else "pass",
            "One or more expert reports are invalid." if report_errors else "Expert report contracts are valid.",
            errors=report_errors,
        )
    )

    reports = _all_reports(decision, expert_reports)
    claims = _all_claims(reports)
    referenced_ids = {item for report in reports for item in report_evidence_ids(report)}
    unknown_ids = sorted(referenced_ids - set(evidence_map)) if known_evidence is not None else []
    cross_deal_ids = sorted(
        evidence_id
        for evidence_id in referenced_ids & set(evidence_map)
        if evidence_map[evidence_id]
        and expected_deal_id
        and evidence_map[evidence_id].get("deal_id") not in (None, expected_deal_id)
    )
    background_as_project = sorted(
        evidence_id
        for evidence_id in referenced_ids & set(evidence_map)
        if evidence_map[evidence_id]
        and str(evidence_map[evidence_id].get("source_type") or "") in BACKGROUND_SOURCE_TYPES
    )
    evidence_failures = unknown_ids + cross_deal_ids + background_as_project
    checks.append(
        _check(
            "evidence.identity",
            "fail" if evidence_failures else "pass",
            "Evidence identity violations found."
            if evidence_failures
            else "All project Evidence references are in scope.",
            unknown_evidence_ids=unknown_ids,
            cross_deal_evidence_ids=cross_deal_ids,
            background_refs_misused_as_evidence=background_as_project,
        )
    )

    unsupported_critical = sorted(
        str(claim.get("claim_id"))
        for claim in claims
        if claim.get("decision_impact") == "critical"
        and claim.get("status") != "missing"
        and not claim.get("evidence_ids")
    )
    missing_critical = sorted(
        str(claim.get("claim_id"))
        for claim in claims
        if claim.get("decision_impact") == "critical" and claim.get("status") == "missing"
    )
    critical_failure = unsupported_critical or (
        missing_critical and decision.get("decision") == "pass"
    )
    checks.append(
        _check(
            "claims.critical_evidence",
            "fail" if critical_failure else "pass",
            "Critical claims are unsupported or missing for a pass decision."
            if critical_failure
            else "Critical claims have project Evidence or are safely reflected in the decision.",
            unsupported_claim_ids=unsupported_critical,
            missing_claim_ids=missing_critical,
        )
    )

    numeric_failures = []
    for claim in claims:
        missing = _numeric_trace_missing_fields(claim)
        if missing:
            numeric_failures.append({"claim_id": claim.get("claim_id"), "missing": missing})
    checks.append(
        _check(
            "financial.numeric_trace",
            "fail" if numeric_failures else "pass",
            "Decision-relevant financial numbers lack identity or trace fields."
            if numeric_failures
            else "Decision-relevant financial numbers are traceable.",
            failures=numeric_failures,
        )
    )

    unresolved_disputes = [
        str(item.get("dispute_id") or "unknown")
        for item in disputes or []
        if str(item.get("severity") or "").lower() in {"critical", "high"}
        and str(item.get("ruling") or item.get("status") or "").lower()
        in {"unresolved", "needs_more_evidence", "open", "pending"}
    ]
    dispute_block = bool(unresolved_disputes and decision.get("decision") == "pass")
    checks.append(
        _check(
            "disputes.resolution",
            "fail" if dispute_block else "pass",
            "Pass decision conflicts with unresolved critical/high disputes."
            if dispute_block
            else "Dispute state is compatible with the decision.",
            unresolved_dispute_ids=unresolved_disputes,
        )
    )

    veto_flags = [
        flag
        for report in reports
        for flag in report.get("veto_flags", [])
        if _unresolved_veto(flag)
    ]
    veto_block = bool(veto_flags and decision.get("decision") == "pass")
    checks.append(
        _check(
            "red_flags.veto",
            "fail" if veto_block else "pass",
            "Pass decision conflicts with unresolved veto flags."
            if veto_block
            else "Veto flags are compatible with the decision.",
            unresolved_veto_flags=veto_flags,
        )
    )

    r3_failure = False
    if r3_plan and r3_plan.get("mode") == "skip":
        skip_checks = r3_plan.get("skip_checks")
        r3_failure = not isinstance(skip_checks, Mapping) or not skip_checks or not all(skip_checks.values())
        if r3_plan.get("requires_human_confirmation_to_skip") and r3_plan.get("human_skip_confirmation") is not True:
            r3_failure = True
    checks.append(
        _check(
            "r3.skip_safety",
            "fail" if r3_failure else "pass",
            "R3 was skipped without satisfying safety conditions."
            if r3_failure
            else "R3 execution/skip state is safe.",
        )
    )

    combined_rendered = f"{rendered_markdown}\n{rendered_html}"
    placeholder_hits = sorted(
        {
            match.group(0)
            for pattern in PLACEHOLDER_PATTERNS
            for match in pattern.finditer(combined_rendered)
        }
    )
    internal_hits = sorted(
        {
            match.group(0)
            for pattern in INTERNAL_PATH_PATTERNS
            for match in pattern.finditer(combined_rendered)
        }
    )
    missing_sections = [title for title in required_section_titles or [] if title not in rendered_markdown]
    completeness_failures = (
        placeholder_hits
        or internal_hits
        or missing_sections
        or not rendered_markdown.strip()
        or not rendered_html.strip()
    )
    checks.append(
        _check(
            "render.completeness",
            "fail" if completeness_failures else "pass",
            "Rendered report is incomplete or leaks internal content."
            if completeness_failures
            else "Rendered Markdown and HTML are complete and clean.",
            placeholders=placeholder_hits,
            internal_path_findings=internal_hits,
            missing_sections=missing_sections,
        )
    )

    consistency_missing = []
    for value in (
        decision.get("decision"),
        decision.get("weighted_agent_score"),
        decision.get("chairman_dimension_score"),
    ):
        text = str(value)
        if text not in rendered_markdown or text not in rendered_html:
            consistency_missing.append(text)
    checks.append(
        _check(
            "render.consistency",
            "fail" if consistency_missing else "pass",
            "JSON/Markdown/HTML decision fields are inconsistent."
            if consistency_missing
            else "JSON/Markdown/HTML decision fields are consistent.",
            missing_values=consistency_missing,
        )
    )

    factcheck_status = "warn"
    factcheck_message = "Factcheck has not been supplied."
    factcheck_details: dict[str, Any] = {}
    if factcheck is not None:
        try:
            normalized_factcheck = validate_factcheck_result(factcheck)
        except (ICContractValidationError, ValueError) as exc:
            factcheck_status = "fail"
            factcheck_message = "Factcheck contract is invalid."
            factcheck_details["errors"] = list(getattr(exc, "errors", [str(exc)]))
        else:
            critical_unsupported = _critical_unsupported_claim_ids(normalized_factcheck, claims)
            factcheck_details["critical_unsupported_claim_ids"] = critical_unsupported
            if normalized_factcheck["status"] == "fail" or critical_unsupported:
                factcheck_status = "fail"
                factcheck_message = "Factcheck blocks publication."
            elif normalized_factcheck["status"] == "warn":
                factcheck_status = "warn"
                factcheck_message = "Factcheck requires human review."
            else:
                factcheck_status = "pass"
                factcheck_message = "Factcheck passed."
    checks.append(_check("factcheck.result", factcheck_status, factcheck_message, **factcheck_details))

    status = _overall(checks)
    blocking_reasons = [item["id"] for item in checks if item["status"] == "fail"]
    return {
        "schema_version": IC_REPORT_QUALITY_SCHEMA,
        "report_id": decision.get("report_id"),
        "report_revision": decision.get("revision", 1),
        "deal_id": decision.get("deal_id"),
        "evidence_snapshot_hash": decision.get("evidence_snapshot_hash"),
        "status": status,
        "allowed_for_human_confirmation": not blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "checks": checks,
        "metrics": {
            "claim_count": len(claims),
            "project_evidence_reference_count": len(referenced_ids),
            "background_knowledge_reference_count": sum(
                len(report.get("background_knowledge_refs", [])) for report in reports
            ),
            "unknown_evidence_count": len(unknown_ids),
            "critical_unsupported_count": len(unsupported_critical),
            "numeric_trace_failure_count": len(numeric_failures),
        },
    }


def build_repair_revision(
    original_report: Mapping[str, Any],
    repaired_report: Mapping[str, Any],
    *,
    factcheck: Mapping[str, Any],
    repair_summary: Sequence[str],
    revised_by: Mapping[str, Any],
) -> dict[str, Any]:
    normalized_factcheck = validate_factcheck_result(factcheck)
    original = deepcopy(dict(original_report))
    repaired = deepcopy(dict(repaired_report))
    errors = []
    if repaired.get("report_id") == original.get("report_id"):
        errors.append("repair_requires_new_report_id")
    if repaired.get("parent_report_id") != original.get("report_id"):
        errors.append("repair_parent_report_id_mismatch")
    if repaired.get("revision") != int(original.get("revision") or 1) + 1:
        errors.append("repair_revision_number_mismatch")
    if not repair_summary:
        errors.append("repair_summary_required")
    if not normalized_factcheck.get("required_repairs"):
        errors.append("factcheck_has_no_required_repairs")
    if errors:
        raise ICContractValidationError(IC_REPORT_REPAIR_REVISION_SCHEMA, errors)
    return {
        "schema_version": IC_REPORT_REPAIR_REVISION_SCHEMA,
        "parent_report_id": original.get("report_id"),
        "report_id": repaired.get("report_id"),
        "revision": repaired.get("revision"),
        "repair_summary": list(repair_summary),
        "required_repairs": deepcopy(normalized_factcheck["required_repairs"]),
        "revised_by": deepcopy(dict(revised_by)),
        "repaired_report": repaired,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


__all__ = [
    "IC_REPORT_FACTCHECK_SCHEMA",
    "IC_REPORT_QUALITY_SCHEMA",
    "IC_REPORT_REPAIR_REVISION_SCHEMA",
    "build_factcheck_input",
    "build_repair_revision",
    "evaluate_report_quality",
    "validate_factcheck_result",
]
