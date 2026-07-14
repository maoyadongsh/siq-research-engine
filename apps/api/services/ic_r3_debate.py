"""Deterministic R3 planning and model-output contracts.

The planner decides what must be debated; Hermes profiles produce the arguments.
It deliberately contains no gateway calls or workflow writes.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from services import ic_report_contracts

R3_DEBATE_PLAN_SCHEMA = "siq_ic_r3_debate_plan_v1"
R3_DEBATE_TURN_SCHEMA = ic_report_contracts.IC_R3_DEBATE_TURN_SCHEMA
R3_DEBATE_VERDICT_SCHEMA = ic_report_contracts.IC_R3_DEBATE_VERDICT_SCHEMA

HIGH_SEVERITIES = {"critical", "high"}
MATERIAL_FLAG_SEVERITIES = {*HIGH_SEVERITIES, "material"}
CLOSED_FLAG_STATUSES = {"accepted", "cleared", "closed", "remediated", "resolved", "waived"}
NEGATIVE_RECOMMENDATIONS = {"reject", "no_go", "pass_on", "caution", "insufficient_evidence"}
POSITIVE_RECOMMENDATIONS = {"support", "pass", "conditional_pass", "conditional_support", "go"}
DEFAULT_MAX_R3_TOPICS = 2
MAX_R3_TOPIC_BUDGET = 5


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dedupe(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _normalized_topic_budget(value: Any) -> int:
    try:
        budget = int(value)
    except (TypeError, ValueError):
        budget = DEFAULT_MAX_R3_TOPICS
    return max(1, min(budget, MAX_R3_TOPIC_BUDGET))


def _normalized_token(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _open_material_red_flag(value: Any) -> bool:
    if value in (None, "", {}, []):
        return False
    if not isinstance(value, Mapping):
        # Legacy reports used unstructured strings for red flags. Without an
        # explicit closure state, fail closed and require R3 review.
        return True
    if value.get("closed") is True or value.get("resolved") is True:
        return False
    if _normalized_token(value.get("status")) in CLOSED_FLAG_STATUSES:
        return False
    severity = _normalized_token(
        value.get("severity") or value.get("level") or value.get("decision_impact") or value.get("materiality")
    )
    flag_type = _normalized_token(value.get("type"))
    return (
        value.get("veto") is True
        or value.get("blocking") is True
        or severity in MATERIAL_FLAG_SEVERITIES
        or flag_type in {"diligence_blocker", "veto"}
    )


def _red_flag_severity(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "high"
    severity = _normalized_token(
        value.get("severity") or value.get("level") or value.get("decision_impact") or value.get("materiality")
    )
    if severity in HIGH_SEVERITIES:
        return severity
    return "high"


def _red_flag_topic(
    *,
    index: int,
    agent_id: str,
    report: Mapping[str, Any],
    red_flag: Any,
) -> dict[str, Any]:
    flag = red_flag if isinstance(red_flag, Mapping) else {"description": str(red_flag)}
    question = next(
        (
            str(flag.get(key)).strip()
            for key in ("question", "description", "issue", "title", "risk", "reason")
            if str(flag.get(key) or "").strip()
        ),
        f"Unresolved material red flag from {agent_id}",
    )
    evidence_ids = _dedupe(
        _as_list(flag.get("evidence_ids"))
        + _as_list(flag.get("counter_evidence_ids"))
        + _as_list(report.get("evidence_ids"))
    )
    claim_ids = _dedupe(_as_list(flag.get("claim_ids")))
    return {
        "dispute_id": f"R3-RED-FLAG-{index:03d}",
        "topic": question,
        "dimension": "legal_red_flag" if agent_id == "siq_ic_legal_scanner" else "material_red_flag",
        "severity": _red_flag_severity(red_flag),
        "resolved": False,
        "agent_ids": [agent_id],
        "claim_ids": claim_ids,
        "evidence_ids": evidence_ids,
        "positions": [
            {
                "agent_id": agent_id,
                "recommendation": report.get("recommendation"),
                "score": report.get("r2_score", report.get("score")),
                "claim_ids": claim_ids,
                "evidence_ids": evidence_ids,
                "red_flags": [red_flag],
            }
        ],
    }


def _aggregate_red_flag_topic(
    *,
    index: int,
    agent_id: str,
    report: Mapping[str, Any],
    red_flags: list[Any],
) -> dict[str, Any]:
    material_flags = [item for item in red_flags if _open_material_red_flag(item)]
    if not material_flags:
        raise ValueError("R3 red-flag topic requires an open material flag")
    descriptions = [
        _red_flag_topic(index=index, agent_id=agent_id, report=report, red_flag=item)["topic"]
        for item in material_flags
    ]
    evidence_ids = _dedupe(
        [
            evidence_id
            for item in material_flags
            for evidence_id in (
                _as_list(item.get("evidence_ids"))
                + _as_list(item.get("counter_evidence_ids"))
                if isinstance(item, Mapping)
                else []
            )
        ]
        + _as_list(report.get("evidence_ids"))
    )
    claim_ids = _dedupe(
        [
            claim_id
            for item in material_flags
            for claim_id in (_as_list(item.get("claim_ids")) if isinstance(item, Mapping) else [])
        ]
    )
    severity = (
        "critical"
        if any(_red_flag_severity(item) == "critical" for item in material_flags)
        else "high"
    )
    return {
        "dispute_id": f"R3-RED-FLAG-{index:03d}",
        "topic": "; ".join(descriptions),
        "dimension": "legal_red_flag" if agent_id == "siq_ic_legal_scanner" else "material_red_flag",
        "severity": severity,
        "resolved": False,
        "agent_ids": [agent_id],
        "claim_ids": claim_ids,
        "evidence_ids": evidence_ids,
        "positions": [
            {
                "agent_id": agent_id,
                "recommendation": report.get("recommendation"),
                "score": report.get("r2_score", report.get("score")),
                "claim_ids": claim_ids,
                "evidence_ids": evidence_ids,
                "red_flags": material_flags,
            }
        ],
        "material_red_flag_count": len(material_flags),
    }


def _topic_agent_ids(topic: Mapping[str, Any]) -> set[str]:
    result = {str(item) for item in _as_list(topic.get("agent_ids")) if str(item).strip()}
    result.update(
        str(item.get("agent_id") or "")
        for item in _as_list(topic.get("positions"))
        if isinstance(item, Mapping) and item.get("agent_id")
    )
    return result


def _deferred_topic_view(topic: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    red_flags = [
        flag
        for position in _as_list(topic.get("positions"))
        if isinstance(position, Mapping)
        for flag in _as_list(position.get("red_flags"))
        if _open_material_red_flag(flag)
    ]
    return {
        "topic_id": _topic_id(topic, index),
        "question": str(topic.get("question") or topic.get("topic") or "R3 deferred issue").strip(),
        "dimension": topic.get("dimension"),
        "severity": str(topic.get("severity") or "medium").lower(),
        "agent_ids": sorted(_topic_agent_ids(topic)),
        "claim_ids": _dedupe(_as_list(topic.get("claim_ids"))),
        "evidence_ids": _dedupe(_as_list(topic.get("evidence_ids"))),
        "material_red_flags": red_flags,
        "deferred_reason": "r3_topic_budget",
    }


def _topic_id(dispute: Mapping[str, Any], index: int) -> str:
    existing = str(dispute.get("dispute_id") or "").strip()
    if existing:
        return existing
    digest = hashlib.sha256(
        json.dumps(dict(dispute), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:12]
    return f"R3TOPIC-{index:03d}-{digest}"


def _position_score(position: Mapping[str, Any]) -> float | None:
    return _number(position.get("score"))


def _recommendation_bucket(position: Mapping[str, Any]) -> int:
    recommendation = str(position.get("recommendation") or "").strip().lower()
    if recommendation in NEGATIVE_RECOMMENDATIONS:
        return -1
    if recommendation in POSITIVE_RECOMMENDATIONS:
        return 1
    return 0


def assign_debate_sides(
    dispute: Mapping[str, Any],
    r2_reports: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str]:
    """Choose sides from actual positions rather than permanent role labels."""

    positions = [item for item in _as_list(dispute.get("positions")) if isinstance(item, Mapping)]
    candidates: list[dict[str, Any]] = []
    for position in positions:
        agent_id = str(position.get("agent_id") or position.get("profile_id") or "").strip()
        if not agent_id:
            continue
        candidates.append(
            {
                "agent_id": agent_id,
                "bucket": _recommendation_bucket(position),
                "score": _position_score(position),
            }
        )

    if not candidates:
        for agent_id, report in r2_reports.items():
            candidates.append(
                {
                    "agent_id": str(agent_id),
                    "bucket": _recommendation_bucket(report),
                    "score": _number(report.get("r2_score", report.get("score"))),
                }
            )
    if not candidates:
        raise ValueError("R3 debate requires at least one expert position")

    def red_key(item: Mapping[str, Any]) -> tuple[int, float, str]:
        score = item.get("score")
        return (int(item.get("bucket") or 0), float(score) if score is not None else 50.0, str(item["agent_id"]))

    def blue_key(item: Mapping[str, Any]) -> tuple[int, float, str]:
        score = item.get("score")
        return (int(item.get("bucket") or 0), float(score) if score is not None else 50.0, str(item["agent_id"]))

    red = min(candidates, key=red_key)
    remaining = [item for item in candidates if item["agent_id"] != red["agent_id"]]
    blue = max(remaining or candidates, key=blue_key)
    if blue["agent_id"] == red["agent_id"]:
        fallback_ids = [str(agent_id) for agent_id in r2_reports if str(agent_id) != red["agent_id"]]
        if fallback_ids:
            blue = {"agent_id": fallback_ids[0]}
    return str(red["agent_id"]), str(blue["agent_id"])


def plan_r3_debate(
    *,
    deal_id: str,
    disputes: list[Mapping[str, Any]],
    r2_reports: Mapping[str, Mapping[str, Any]],
    allow_skip: bool = True,
    evidence_quality: Mapping[str, Any] | None = None,
    policy_allows_skip: bool = True,
    max_topics: int = DEFAULT_MAX_R3_TOPICS,
) -> dict[str, Any]:
    unresolved = [item for item in disputes if not bool(item.get("resolved"))]
    contested_claims = [
        claim
        for report in r2_reports.values()
        for claim in _as_list(report.get("claims"))
        if isinstance(claim, Mapping)
        and (
            str(claim.get("status") or "").lower() == "contested"
            or (
                str(claim.get("decision_impact") or "").lower() in {"critical", "material"}
                and str(claim.get("status") or "").lower() == "missing"
            )
        )
    ]
    veto_flags = [
        item
        for report in r2_reports.values()
        for item in _as_list(report.get("veto_flags"))
        if item not in (None, "", {}, [])
    ]
    material_red_flags = [
        (str(agent_id), report, red_flag)
        for agent_id, report in r2_reports.items()
        if isinstance(report, Mapping)
        for red_flag in _as_list(report.get("red_flags"))
        if _open_material_red_flag(red_flag)
    ]
    recommendation_buckets = {
        _recommendation_bucket(report)
        for report in r2_reports.values()
        if _recommendation_bucket(report)
    }
    opposing_recommendations = {-1, 1}.issubset(recommendation_buckets)
    unresolved_high_risk = (
        any(str(item.get("severity") or "").lower() in HIGH_SEVERITIES for item in unresolved)
        or bool(veto_flags)
        or bool(material_red_flags)
    )
    quality = dict(evidence_quality or {})
    quality_status = str(quality.get("gate_status") or quality.get("status") or "").lower()
    evidence_coverage_ready = quality_status in {"pass", "passed", "ready", "ok"}
    rulings_closed = not unresolved
    skip_checks = {
        "no_material_contested_claims": not contested_claims and not opposing_recommendations,
        "no_unresolved_high_risk": not unresolved_high_risk,
        "evidence_coverage_ready": evidence_coverage_ready,
        "r1_5_rulings_closed": rulings_closed,
        "no_veto_flags": not veto_flags,
        "no_unresolved_material_red_flags": not material_red_flags,
        "policy_allows_skip": bool(policy_allows_skip and allow_skip),
    }
    challenged_agents = {
        str(agent_id)
        for agent_id, report in r2_reports.items()
        if _as_list(report.get("challenged_rulings"))
        or _as_list(report.get("remaining_questions"))
        or _as_list(report.get("risk_flags"))
    }
    topic_inputs = unresolved or [
        {
            "dispute_id": f"R3-R2-{index:03d}",
            "topic": f"R2 remaining challenge from {agent_id}",
            "dimension": "r2_revision",
            "severity": "medium",
            "resolved": False,
            "agent_ids": [agent_id],
            "evidence_ids": _as_list(r2_reports[agent_id].get("evidence_ids")),
            "positions": [{**r2_reports[agent_id], "agent_id": agent_id}],
        }
        for index, agent_id in enumerate(sorted(challenged_agents), start=1)
    ]
    if not topic_inputs and contested_claims:
        topic_inputs = [
            {
                "dispute_id": f"R3-CLAIM-{index:03d}",
                "topic": str(claim.get("topic") or claim.get("conclusion") or "material contested claim"),
                "dimension": "claim",
                "severity": "high" if claim.get("decision_impact") == "critical" else "medium",
                "resolved": False,
                "claim_ids": [claim.get("claim_id")],
                "evidence_ids": _as_list(claim.get("evidence_ids")),
                "positions": list(r2_reports.values()),
            }
            for index, claim in enumerate(contested_claims, start=1)
        ]
    represented_agents = {
        agent_id
        for topic in topic_inputs
        for agent_id in _topic_agent_ids(topic)
    }
    flags_by_unrepresented_agent: dict[str, tuple[Mapping[str, Any], list[Any]]] = {}
    for agent_id, report, red_flag in material_red_flags:
        if agent_id in represented_agents:
            continue
        current_report, flags = flags_by_unrepresented_agent.setdefault(agent_id, (report, []))
        flags.append(red_flag)
        flags_by_unrepresented_agent[agent_id] = (current_report, flags)
    topic_inputs.extend(
        _aggregate_red_flag_topic(
            index=len(topic_inputs) + index,
            agent_id=agent_id,
            report=report,
            red_flags=flags,
        )
        for index, (agent_id, (report, flags)) in enumerate(
            sorted(flags_by_unrepresented_agent.items()),
            start=1,
        )
    )
    safety_failures = [key for key, passed in skip_checks.items() if not passed]
    if not topic_inputs and safety_failures:
        topic_inputs = [
            {
                "dispute_id": "R3-SAFETY-GATE-001",
                "topic": "R3 skip safety gate failed: " + ", ".join(safety_failures),
                "dimension": "workflow_quality",
                "severity": "high" if unresolved_high_risk or veto_flags else "medium",
                "resolved": False,
                "evidence_ids": [],
                "positions": list(r2_reports.values()),
            }
        ]

    if not topic_inputs and all(skip_checks.values()):
        return {
            "schema_version": R3_DEBATE_PLAN_SCHEMA,
            "deal_id": deal_id,
            "mode": "skip",
            "skip_reason": {
                "code": "no_material_contested_claims",
                "message": (
                    "R1.5 disputes are closed and R2 contains no challenged ruling, remaining high-risk question, "
                    "or unresolved material red flag."
                ),
                "policy_allowed": True,
            },
            "skip_checks": skip_checks,
            "requires_human_confirmation_to_skip": False,
            "topics": [],
            "blocking": False,
            "topic_budget": _normalized_topic_budget(max_topics),
            "candidate_topic_count": 0,
            "selected_topic_count": 0,
            "deferred_topic_count": 0,
            "deferred_topics": [],
            "topic_selection_policy": "stable_material_topic_budget_v1",
        }

    topic_budget = _normalized_topic_budget(max_topics)
    candidate_topics = list(topic_inputs)
    selected_topic_inputs = candidate_topics[:topic_budget]
    deferred_topics = [
        _deferred_topic_view(topic, index=index)
        for index, topic in enumerate(candidate_topics[topic_budget:], start=topic_budget + 1)
    ]
    has_high = any(
        str(item.get("severity") or "").lower() in HIGH_SEVERITIES
        for item in candidate_topics
    )
    blocking = bool(unresolved_high_risk or has_high)
    mode = "full" if blocking or len(candidate_topics) > 2 else "short"
    topics: list[dict[str, Any]] = []
    for index, dispute in enumerate(selected_topic_inputs, start=1):
        red_agent_id, blue_agent_id = assign_debate_sides(dispute, r2_reports)
        topics.append(
            {
                "topic_id": _topic_id(dispute, index),
                "question": str(dispute.get("question") or dispute.get("topic") or "R3 contested issue").strip(),
                "dimension": dispute.get("dimension"),
                "severity": str(dispute.get("severity") or "medium").lower(),
                "dispute_id": dispute.get("dispute_id"),
                "red_agent_id": red_agent_id,
                "blue_agent_id": blue_agent_id,
                "claim_ids": _dedupe(_as_list(dispute.get("claim_ids"))),
                "evidence_ids": _dedupe(_as_list(dispute.get("evidence_ids"))),
                "positions": _as_list(dispute.get("positions")),
            }
        )
    return {
        "schema_version": R3_DEBATE_PLAN_SCHEMA,
        "deal_id": deal_id,
        "mode": mode,
        "skip_reason": None,
        "topics": topics,
        "skip_checks": skip_checks,
        "skip_blocking_reasons": safety_failures,
        "blocking": blocking,
        "topic_budget": topic_budget,
        "candidate_topic_count": len(candidate_topics),
        "selected_topic_count": len(topics),
        "deferred_topic_count": len(deferred_topics),
        "deferred_topics": deferred_topics,
        "topic_selection_policy": "stable_material_topic_budget_v1",
    }


def validate_debate_turn(
    parsed: Mapping[str, Any],
    *,
    expected_agent_id: str,
    expected_turn_type: str,
    required_response_ids: list[str] | None = None,
) -> dict[str, Any]:
    argument = str(parsed.get("argument") or parsed.get("thesis") or parsed.get("response") or "").strip()
    if not argument:
        raise ValueError("R3 Hermes output contract invalid: argument_missing")
    evidence_ids = _dedupe(_as_list(parsed.get("evidence_ids")))
    if not evidence_ids:
        raise ValueError("R3 Hermes output contract invalid: evidence_ids_missing")
    responds_to = _dedupe(_as_list(parsed.get("responds_to_argument_ids")))
    missing_refs = [item for item in (required_response_ids or []) if item not in responds_to]
    if missing_refs:
        raise ValueError("R3 Hermes output contract invalid: opponent_argument_reference_missing")
    normalized = dict(parsed)
    normalized.update(
        {
            "schema_version": R3_DEBATE_TURN_SCHEMA,
            "agent_id": expected_agent_id,
            "turn_type": expected_turn_type,
            "argument": argument,
            "claim_ids": _dedupe(_as_list(parsed.get("claim_ids"))),
            "evidence_ids": evidence_ids,
            "responds_to_argument_ids": responds_to,
            "unanswered_points": _dedupe(_as_list(parsed.get("unanswered_points"))),
        }
    )
    return normalized


def validate_debate_verdict(parsed: Mapping[str, Any], *, topic_id: str) -> dict[str, Any]:
    outcome = str(parsed.get("outcome") or parsed.get("verdict") or "").strip().lower()
    if outcome not in {"red_prevails", "blue_prevails", "synthesize", "needs_more_evidence", "unresolved"}:
        raise ValueError("R3 chairman verdict contract invalid: outcome")
    rationale = str(parsed.get("rationale") or "").strip()
    if not rationale:
        raise ValueError("R3 chairman verdict contract invalid: rationale_missing")
    accepted = _dedupe(_as_list(parsed.get("accepted_argument_ids")))
    rejected = _dedupe(_as_list(parsed.get("rejected_argument_ids")))
    if not accepted and not rejected:
        raise ValueError("R3 chairman verdict contract invalid: argument_assessment_missing")
    return {
        **dict(parsed),
        "schema_version": R3_DEBATE_VERDICT_SCHEMA,
        "topic_id": topic_id,
        "outcome": outcome,
        "rationale": rationale,
        "accepted_argument_ids": accepted,
        "rejected_argument_ids": rejected,
        "evidence_ids": _dedupe(_as_list(parsed.get("evidence_ids"))),
        "decision_impact": str(parsed.get("decision_impact") or "").strip(),
        "required_followups": _dedupe(_as_list(parsed.get("required_followups"))),
        "resolved": outcome not in {"needs_more_evidence", "unresolved"},
    }


__all__ = [
    "R3_DEBATE_PLAN_SCHEMA",
    "R3_DEBATE_TURN_SCHEMA",
    "R3_DEBATE_VERDICT_SCHEMA",
    "assign_debate_sides",
    "plan_r3_debate",
    "validate_debate_turn",
    "validate_debate_verdict",
]
