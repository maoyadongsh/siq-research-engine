"""SIQ IC scoring helpers."""

from __future__ import annotations

from typing import Any

from services import ic_policy


WEIGHTED_SCORING_SCHEMA = "siq_ic_weighted_scoring_v1"

ROLE_AGENT_FALLBACK = {
    "chairman": "siq_ic_chairman",
    "strategy": "siq_ic_strategist",
    "sector": "siq_ic_sector_expert",
    "finance": "siq_ic_finance_auditor",
    "legal": "siq_ic_legal_scanner",
    "risk": "siq_ic_risk_controller",
}


def numeric(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def round_score(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


def role_agent_ids(policy: dict[str, Any]) -> dict[str, str]:
    roles = policy.get("roles") if isinstance(policy.get("roles"), dict) else {}
    mapping: dict[str, str] = {}
    for role, fallback in ROLE_AGENT_FALLBACK.items():
        role_payload = roles.get(role) if isinstance(roles.get(role), dict) else {}
        mapping[role] = ic_policy.canonical_ic_profile_id(str(role_payload.get("agent_id") or fallback))
    return mapping


def agent_role(agent_id: str, policy: dict[str, Any]) -> str:
    for role, role_agent_id in role_agent_ids(policy).items():
        if role_agent_id == agent_id:
            return role
    return agent_id.removeprefix("siq_ic_")


def calculate_weighted_agent_score(
    *,
    policy: dict[str, Any],
    r1_reports: dict[str, dict[str, Any]],
    r2_reports: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    weights = policy.get("weights") if isinstance(policy.get("weights"), dict) else {}
    role_agents = role_agent_ids(policy)
    scoring_inputs: list[dict[str, Any]] = []
    warnings: list[str] = []
    weighted_sum = 0.0
    active_weight_sum = 0.0
    configured_weight_sum = 0.0

    for role, raw_weight in weights.items():
        weight = numeric(raw_weight)
        if weight is None or weight <= 0:
            continue
        configured_weight_sum += weight
        agent_id = role_agents.get(str(role), ROLE_AGENT_FALLBACK.get(str(role), str(role)))
        report = r2_reports.get(agent_id) or r1_reports.get(agent_id) or {}
        score = numeric(report.get("r2_score", report.get("score")))
        scoring_inputs.append({
            "role": role,
            "agent_id": agent_id,
            "weight": weight,
            "score": score,
            "source_round": report.get("round_name"),
        })
        if score is None:
            warnings.append(f"weighted_score_missing:{role}:{agent_id}")
            continue
        weighted_sum += score * weight
        active_weight_sum += weight

    weighted_score = None
    if active_weight_sum > 0:
        weighted_score = round_score(weighted_sum / active_weight_sum)
        if active_weight_sum < configured_weight_sum:
            warnings.append(f"weighted_score_normalized_weight_sum:{active_weight_sum:.2f}")
    else:
        warnings.append("weighted_score_no_active_inputs")

    return {
        "schema_version": WEIGHTED_SCORING_SCHEMA,
        "weighted_agent_score": weighted_score,
        "inputs": scoring_inputs,
        "warnings": warnings,
        "active_weight_sum": round_score(active_weight_sum),
        "configured_weight_sum": round_score(configured_weight_sum),
        "generation_mode": "policy_weighted_agent_score_v1",
    }


def threshold_result(score: float, policy: dict[str, Any]) -> str:
    thresholds = policy.get("thresholds") if isinstance(policy.get("thresholds"), dict) else {}
    pass_min = numeric(thresholds.get("pass")) or 70.0
    review_min = numeric(thresholds.get("review_min")) or 68.0
    review_max = numeric(thresholds.get("review_max")) or pass_min - 1
    if score >= pass_min:
        return "pass"
    if review_min <= score <= review_max:
        return "review"
    return "fail"
