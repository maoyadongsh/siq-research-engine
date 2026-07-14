"""Formal claim, expert report, and phase artifact contracts for IC workflows."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from services import ic_policy, ic_profile_contract
from services.ic_contract_validation import (
    ICContractValidationError,
    combine_validation_errors,
    require_identity,
    validate_schema,
)
from services.ic_task_contracts import ROLE_OUTPUT_SCHEMAS, STARTUP_RETRIEVAL_GATE_SCHEMA

IC_CLAIM_SCHEMA = "siq_ic_claim_v1"
IC_EXPERT_REPORT_SCHEMA = "siq_ic_expert_report_v2"
IC_R0_READINESS_SCHEMA = "siq_ic_r0_readiness_v1"
IC_R1_5_DISPUTE_SCHEMA = "siq_ic_r1_5_dispute_v1"
IC_R1_5_CHAIRMAN_RULINGS_SCHEMA = "siq_ic_r1_5_chairman_rulings_v2"
IC_R2_REVISION_SCHEMA = "siq_ic_r2_revision_v1"
IC_R3_PLAN_SCHEMA = "siq_ic_r3_plan_v1"
IC_R3_DEBATE_SCHEMA = "siq_ic_r3_debate_v1"
IC_R3_DEBATE_TURN_SCHEMA = "siq_ic_r3_debate_turn_v1"
IC_R3_DEBATE_VERDICT_SCHEMA = "siq_ic_r3_debate_verdict_v1"
IC_R4_DECISION_SCHEMA = "siq_ic_r4_decision_v2"

RECOMMENDATIONS = (
    "support",
    "conditional_support",
    "review",
    "reject",
    "insufficient_evidence",
)
CLAIM_STATUSES = ("verified", "derived", "assumed", "contested", "missing")
CONFIDENCE_LEVELS = ("high", "medium", "low")
DECISION_IMPACTS = ("critical", "material", "supporting")
EXPERT_REPORT_PHASES = ("R1A", "R1B", "R2")

EVIDENCE_ID_SCHEMA = {
    "type": "string",
    "pattern": r"^EVID-[A-Za-z0-9][A-Za-z0-9:_-]{2,190}$",
}
BACKGROUND_REF_ID_SCHEMA = {
    "type": "string",
    "pattern": r"^KBREF-[A-Z0-9][A-Z0-9-]{5,95}$",
}
CLAIM_ID_SCHEMA = {
    "type": "string",
    "pattern": r"^CLM-[A-Z0-9][A-Z0-9-]{5,95}$",
}
REPORT_ID_SCHEMA = {
    "type": "string",
    "pattern": r"^ICRPT-[A-Z0-9][A-Z0-9-]{7,95}$",
}
DEAL_ID_SCHEMA = {"type": "string", "pattern": r"^[A-Z0-9][A-Z0-9_-]{2,96}$"}
SNAPSHOT_HASH_SCHEMA = {"type": "string", "pattern": r"^[a-fA-F0-9]{64}$"}
NON_EMPTY_STRING = {"type": "string", "minLength": 1}
STRING_LIST = {"type": "array", "items": NON_EMPTY_STRING, "uniqueItems": True}
EVIDENCE_LIST = {"type": "array", "items": EVIDENCE_ID_SCHEMA, "uniqueItems": True}
BACKGROUND_REF_ID_LIST = {"type": "array", "items": BACKGROUND_REF_ID_SCHEMA, "uniqueItems": True}
CLAIM_ID_LIST = {"type": "array", "items": CLAIM_ID_SCHEMA, "uniqueItems": True}
STRUCTURED_REQUIRED = {
    "anyOf": [
        {"type": "object", "minProperties": 1},
        {"type": "array", "minItems": 1},
        {"type": "string", "minLength": 1},
    ]
}

IC_CLAIM_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_CLAIM_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "claim_id",
        "topic",
        "conclusion",
        "status",
        "evidence_ids",
        "counter_evidence_ids",
        "calculation_trace_ids",
        "background_knowledge_ref_ids",
        "methodology_ref_ids",
        "confidence",
        "decision_impact",
        "period",
        "currency",
        "unit",
    ],
    "properties": {
        "claim_id": CLAIM_ID_SCHEMA,
        "topic": {"type": "string", "minLength": 1, "maxLength": 120},
        "conclusion": {"type": "string", "minLength": 1, "maxLength": 8000},
        "status": {"type": "string", "enum": list(CLAIM_STATUSES)},
        "evidence_ids": EVIDENCE_LIST,
        "counter_evidence_ids": EVIDENCE_LIST,
        "calculation_trace_ids": STRING_LIST,
        "background_knowledge_ref_ids": BACKGROUND_REF_ID_LIST,
        "methodology_ref_ids": BACKGROUND_REF_ID_LIST,
        "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
        "decision_impact": {"type": "string", "enum": list(DECISION_IMPACTS)},
        "period": {"type": ["string", "null"], "maxLength": 80},
        "currency": {"type": ["string", "null"], "maxLength": 16},
        "unit": {"type": ["string", "null"], "maxLength": 32},
        "value": {"type": ["number", "string", "null"]},
        "assumption": {"type": ["string", "null"], "maxLength": 4000},
        "verification_method": {"type": ["string", "null"], "maxLength": 4000},
    },
}

SCORECARD_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["dimension", "score", "weight", "rationale", "claim_ids", "evidence_ids", "confidence"],
    "properties": {
        "dimension": {"type": "string", "minLength": 1, "maxLength": 100},
        "score": {"type": "number", "minimum": 0, "maximum": 100},
        "weight": {"type": "number", "exclusiveMinimum": 0, "maximum": 100},
        "rationale": {"type": "string", "minLength": 1, "maxLength": 4000},
        "claim_ids": {**CLAIM_ID_LIST, "minItems": 1},
        "evidence_ids": {**EVIDENCE_LIST, "minItems": 1},
        "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
    },
}

BASE_REPORT_REQUIRED = [
    "schema_version",
    "report_id",
    "deal_id",
    "phase",
    "agent_id",
    "research_identity",
    "evidence_snapshot_hash",
    "recommendation",
    "score",
    "confidence",
    "claims",
    "scorecard",
    "red_flags",
    "open_questions",
    "required_followups",
    "executive_summary",
    "methodology",
    "background_knowledge_refs",
    "methodology_refs",
    "startup_receipt_id",
    "startup_retrieval_gate",
    "limitations",
    "generation_mode",
    "revision",
    "parent_report_id",
    "created_at",
]

BASE_REPORT_PROPERTIES: dict[str, Any] = {
    "schema_version": {"const": IC_EXPERT_REPORT_SCHEMA},
    "report_id": REPORT_ID_SCHEMA,
    "workflow_run_id": {"type": "string", "pattern": r"^ICRUN-[A-Z0-9][A-Z0-9-]{7,95}$"},
    "deal_id": DEAL_ID_SCHEMA,
    "phase": {"type": "string", "enum": list(EXPERT_REPORT_PHASES)},
    "agent_id": {"type": "string", "enum": list(ic_policy.IC_PROFILE_IDS)},
    "research_identity": {"type": "object", "minProperties": 1},
    "evidence_snapshot_hash": SNAPSHOT_HASH_SCHEMA,
    "recommendation": {"type": "string", "enum": list(RECOMMENDATIONS)},
    "score": {"type": "number", "minimum": 0, "maximum": 100},
    "confidence": {"type": "string", "enum": list(CONFIDENCE_LEVELS)},
    "claims": {"type": "array", "items": IC_CLAIM_JSON_SCHEMA, "minItems": 1},
    "scorecard": {"type": "array", "items": SCORECARD_ITEM_SCHEMA, "minItems": 1},
    "red_flags": {"type": "array", "items": {"type": ["object", "string"]}},
    "open_questions": {"type": "array", "items": {"type": ["object", "string"]}},
    "required_followups": {"type": "array", "items": {"type": ["object", "string"]}},
    "executive_summary": {"type": "string", "minLength": 1, "maxLength": 12000},
    "methodology": {**STRING_LIST, "minItems": 1},
    "background_knowledge_refs": {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["ref_id", "collection", "locator", "title", "usage"],
            "properties": {
                "ref_id": BACKGROUND_REF_ID_SCHEMA,
                "collection": {"type": "string", "minLength": 1, "maxLength": 128},
                "locator": {"type": "string", "minLength": 1, "maxLength": 500},
                "title": {"type": "string", "minLength": 1, "maxLength": 500},
                "usage": {"enum": ["background", "methodology", "comparable_context"]},
            },
        },
    },
    "methodology_refs": {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["ref_id", "collection", "locator", "title", "usage"],
            "properties": {
                "ref_id": BACKGROUND_REF_ID_SCHEMA,
                "collection": {"type": "string", "minLength": 1, "maxLength": 128},
                "locator": {"type": "string", "minLength": 1, "maxLength": 500},
                "title": {"type": "string", "minLength": 1, "maxLength": 500},
                "usage": {"const": "methodology"},
            },
        },
    },
    "startup_receipt_id": {"type": "string", "minLength": 1, "maxLength": 160},
    "startup_retrieval_gate": STARTUP_RETRIEVAL_GATE_SCHEMA,
    "limitations": {"type": "array", "items": NON_EMPTY_STRING},
    "generation_mode": {
        "type": "string",
        "enum": ["model", "deterministic_preview", "deterministic_recovery", "human_authored"],
    },
    "revision": {"type": "integer", "minimum": 1},
    "parent_report_id": {"type": ["string", "null"], "pattern": r"^ICRPT-[A-Z0-9][A-Z0-9-]{7,95}$"},
    "created_at": {"type": "string", "format": "date-time"},
}

IC_EXPERT_REPORT_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_EXPERT_REPORT_SCHEMA,
    "type": "object",
    "additionalProperties": True,
    "required": BASE_REPORT_REQUIRED,
    "properties": BASE_REPORT_PROPERTIES,
}

ROLE_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "siq_ic_master_coordinator": (
        "readiness",
        "due_diligence_plan",
        "task_assignments",
        "progress_summary",
        "quality_summary",
        "audit_summary",
    ),
    "siq_ic_strategist": (
        "policy_assessment",
        "cycle_position",
        "capital_flow_signals",
        "strategic_fit",
        "scenario_matrix",
        "exit_window",
    ),
    "siq_ic_sector_expert": (
        "market_sizing",
        "competitor_matrix",
        "technology_routes",
        "value_chain",
        "market_share_evidence",
        "industry_lifecycle",
    ),
    "siq_ic_finance_auditor": (
        "historical_financials",
        "financial_reconciliations",
        "quality_of_earnings",
        "cash_flow_assessment",
        "forecast_scenarios",
        "valuation_scenarios",
        "sensitivity_analysis",
        "calculation_trace_ids",
    ),
    "siq_ic_legal_scanner": (
        "legal_issues",
        "legal_basis",
        "severity",
        "remediation",
        "closing_conditions",
        "term_sheet_protections",
        "unresolved_legal_questions",
    ),
    "siq_ic_risk_controller": (
        "risk_register",
        "counter_theses",
        "stress_scenarios",
        "risk_transmission",
        "leading_indicators",
        "warning_thresholds",
        "stop_loss_thresholds",
        "veto_flags",
    ),
    "siq_ic_chairman": (
        "consensus",
        "disputes",
        "rulings",
        "six_dimension_scorecard",
        "weighted_agent_score",
        "chairman_dimension_score",
        "chairman_qualitative_decision",
        "conditions",
        "monitoring_metrics",
        "decision",
    ),
}

IC_EXPERT_REPORT_JSON_SCHEMA["allOf"] = [
    {
        "if": {
            "properties": {"agent_id": {"const": agent_id}},
            "required": ["agent_id"],
        },
        "then": {"required": list(fields)},
    }
    for agent_id, fields in ROLE_REQUIRED_FIELDS.items()
]

ROLE_FORBIDDEN_FIELDS: dict[str, tuple[str, ...]] = {
    "siq_ic_master_coordinator": ("valuation_scenarios", "legal_opinion", "decision"),
}
ROLE_EMPTY_ALLOWED_FIELDS = {
    "unresolved_legal_questions",
    "veto_flags",
    "disputes",
    "rulings",
    "conditions",
}

R0_READINESS_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_R0_READINESS_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "workflow_run_id",
        "deal_id",
        "agent_id",
        "research_identity",
        "evidence_snapshot_hash",
        "readiness",
        "material_completeness",
        "evidence_gaps",
        "due_diligence_plan",
        "task_assignments",
        "blocking_reasons",
        "created_at",
    ],
    "properties": {
        "schema_version": {"const": IC_R0_READINESS_SCHEMA},
        "workflow_run_id": BASE_REPORT_PROPERTIES["workflow_run_id"],
        "deal_id": DEAL_ID_SCHEMA,
        "agent_id": {"const": "siq_ic_master_coordinator"},
        "research_identity": {"type": "object", "minProperties": 1},
        "evidence_snapshot_hash": SNAPSHOT_HASH_SCHEMA,
        "readiness": {"enum": ["ready", "blocked", "needs_more_evidence"]},
        "material_completeness": {"type": "object", "minProperties": 1},
        "evidence_gaps": {"type": "array", "items": {"type": ["object", "string"]}},
        "due_diligence_plan": {"type": "array", "items": {"type": ["object", "string"]}, "minItems": 1},
        "task_assignments": {"type": "array", "items": {"type": "object"}, "minItems": 1},
        "blocking_reasons": STRING_LIST,
        "created_at": {"type": "string", "format": "date-time"},
    },
}

R1_5_DISPUTE_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_R1_5_DISPUTE_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "dispute_id",
        "workflow_run_id",
        "deal_id",
        "evidence_snapshot_hash",
        "question",
        "severity",
        "positions",
        "evidence_ids",
        "counter_evidence_ids",
        "ruling",
        "rationale",
        "accepted_claim_ids",
        "rejected_claim_ids",
        "required_followups",
        "decision_impact",
        "created_at",
    ],
    "properties": {
        "schema_version": {"const": IC_R1_5_DISPUTE_SCHEMA},
        "dispute_id": {"type": "string", "pattern": r"^DISP-[A-Z0-9][A-Z0-9_-]{5,95}$"},
        "workflow_run_id": BASE_REPORT_PROPERTIES["workflow_run_id"],
        "deal_id": DEAL_ID_SCHEMA,
        "evidence_snapshot_hash": SNAPSHOT_HASH_SCHEMA,
        "question": NON_EMPTY_STRING,
        "severity": {"enum": ["critical", "high", "medium", "low"]},
        "positions": {"type": "array", "items": {"type": "object", "minProperties": 1}, "minItems": 1},
        "evidence_ids": EVIDENCE_LIST,
        "counter_evidence_ids": EVIDENCE_LIST,
        "ruling": {"enum": ["accept_a", "accept_b", "synthesize", "needs_more_evidence", "unresolved"]},
        "rationale": NON_EMPTY_STRING,
        "accepted_claim_ids": CLAIM_ID_LIST,
        "rejected_claim_ids": CLAIM_ID_LIST,
        "required_followups": {"type": "array", "items": {"type": ["object", "string"]}},
        "decision_impact": NON_EMPTY_STRING,
        "created_at": {"type": "string", "format": "date-time"},
    },
}

R1_5_CHAIRMAN_RULINGS_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_R1_5_CHAIRMAN_RULINGS_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": ["rulings"],
    "properties": {
        "rulings": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "dispute_id",
                    "ruling",
                    "rationale",
                    "required_followups",
                    "evidence_ids",
                    "counter_evidence_ids",
                    "accepted_claim_ids",
                    "rejected_claim_ids",
                    "decision_impact",
                ],
                "properties": {
                    "dispute_id": NON_EMPTY_STRING,
                    "ruling": {
                        "enum": [
                            "accept_a",
                            "accept_b",
                            "synthesize",
                            "needs_more_evidence",
                            "unresolved",
                            "resolved_with_conditions",
                            "resolved_no_followup",
                        ]
                    },
                    "rationale": NON_EMPTY_STRING,
                    "required_followups": STRING_LIST,
                    "evidence_ids": STRING_LIST,
                    "counter_evidence_ids": STRING_LIST,
                    "accepted_claim_ids": STRING_LIST,
                    "rejected_claim_ids": STRING_LIST,
                    "decision_impact": NON_EMPTY_STRING,
                },
            },
        }
    },
}

R2_REVISION_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_R2_REVISION_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "report",
        "r1_score",
        "r2_score",
        "score_change",
        "changed_claims",
        "unchanged_claims",
        "accepted_rulings",
        "challenged_rulings",
        "new_evidence_ids",
        "closed_questions",
        "remaining_questions",
        "revision_rationale",
    ],
    "properties": {
        "schema_version": {"const": IC_R2_REVISION_SCHEMA},
        "report": IC_EXPERT_REPORT_JSON_SCHEMA,
        "r1_score": {"type": "number", "minimum": 0, "maximum": 100},
        "r2_score": {"type": "number", "minimum": 0, "maximum": 100},
        "score_change": {"type": "number", "minimum": -100, "maximum": 100},
        "changed_claims": CLAIM_ID_LIST,
        "unchanged_claims": CLAIM_ID_LIST,
        "accepted_rulings": STRING_LIST,
        "challenged_rulings": STRING_LIST,
        "new_evidence_ids": EVIDENCE_LIST,
        "closed_questions": STRING_LIST,
        "remaining_questions": STRING_LIST,
        "revision_rationale": NON_EMPTY_STRING,
    },
}

R3_PLAN_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_R3_PLAN_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "mode",
        "reason_codes",
        "topics",
        "estimated_rounds",
        "requires_human_confirmation_to_skip",
    ],
    "properties": {
        "schema_version": {"const": IC_R3_PLAN_SCHEMA},
        "mode": {"enum": ["skip", "short", "full"]},
        "reason_codes": {**STRING_LIST, "minItems": 1},
        "topics": {"type": "array", "items": {"type": ["object", "string"]}},
        "estimated_rounds": {"type": "integer", "minimum": 0, "maximum": 10},
        "requires_human_confirmation_to_skip": {"type": "boolean"},
        "skip_checks": {"type": "object", "additionalProperties": {"type": "boolean"}},
        "human_skip_confirmation": {"type": "boolean"},
    },
}

R3_DEBATE_TURN_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_R3_DEBATE_TURN_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "argument",
        "claim_ids",
        "evidence_ids",
        "responds_to_argument_ids",
        "unanswered_points",
    ],
    "properties": {
        "argument": NON_EMPTY_STRING,
        "claim_ids": STRING_LIST,
        "evidence_ids": {**STRING_LIST, "minItems": 1},
        "responds_to_argument_ids": STRING_LIST,
        "unanswered_points": STRING_LIST,
    },
}

R3_DEBATE_VERDICT_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_R3_DEBATE_VERDICT_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "outcome",
        "rationale",
        "accepted_argument_ids",
        "rejected_argument_ids",
        "evidence_ids",
        "decision_impact",
        "required_followups",
    ],
    "properties": {
        "outcome": {
            "enum": [
                "red_prevails",
                "blue_prevails",
                "synthesize",
                "needs_more_evidence",
                "unresolved",
            ]
        },
        "rationale": NON_EMPTY_STRING,
        "accepted_argument_ids": STRING_LIST,
        "rejected_argument_ids": STRING_LIST,
        "evidence_ids": STRING_LIST,
        "decision_impact": NON_EMPTY_STRING,
        "required_followups": STRING_LIST,
    },
    "anyOf": [
        {"properties": {"accepted_argument_ids": {"minItems": 1}}},
        {"properties": {"rejected_argument_ids": {"minItems": 1}}},
    ],
}

DEBATE_TURN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "argument_id",
        "round",
        "speaker",
        "argument",
        "claim_ids",
        "evidence_ids",
        "responds_to_argument_ids",
        "unanswered_points",
    ],
    "properties": {
        "argument_id": {"type": "string", "pattern": r"^ARG-[A-Z0-9][A-Z0-9-]{5,95}$"},
        "round": {"type": "integer", "minimum": 1, "maximum": 10},
        "speaker": {"type": "string", "enum": list(ic_policy.IC_PROFILE_IDS)},
        "argument": NON_EMPTY_STRING,
        "claim_ids": CLAIM_ID_LIST,
        "evidence_ids": EVIDENCE_LIST,
        "responds_to_argument_ids": {
            "type": "array",
            "items": {"type": "string", "pattern": r"^ARG-[A-Z0-9][A-Z0-9-]{5,95}$"},
            "uniqueItems": True,
        },
        "unanswered_points": STRING_LIST,
    },
}

R3_DEBATE_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_R3_DEBATE_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "debate_id",
        "workflow_run_id",
        "deal_id",
        "evidence_snapshot_hash",
        "topic",
        "red_team",
        "blue_team",
        "rounds",
        "chairman_verdict",
        "status",
        "created_at",
    ],
    "properties": {
        "schema_version": {"const": IC_R3_DEBATE_SCHEMA},
        "debate_id": {"type": "string", "pattern": r"^DEB-[A-Z0-9][A-Z0-9-]{5,95}$"},
        "workflow_run_id": BASE_REPORT_PROPERTIES["workflow_run_id"],
        "deal_id": DEAL_ID_SCHEMA,
        "evidence_snapshot_hash": SNAPSHOT_HASH_SCHEMA,
        "topic": NON_EMPTY_STRING,
        "red_team": {
            "type": "array",
            "items": {"enum": list(ic_policy.IC_PROFILE_IDS)},
            "minItems": 1,
            "uniqueItems": True,
        },
        "blue_team": {
            "type": "array",
            "items": {"enum": list(ic_policy.IC_PROFILE_IDS)},
            "minItems": 1,
            "uniqueItems": True,
        },
        "rounds": {"type": "array", "items": DEBATE_TURN_SCHEMA, "minItems": 2},
        "chairman_verdict": {
            "type": "object",
            "additionalProperties": False,
            "required": ["ruling", "rationale", "accepted_argument_ids", "rejected_argument_ids", "decision_impact"],
            "properties": {
                "ruling": NON_EMPTY_STRING,
                "rationale": NON_EMPTY_STRING,
                "accepted_argument_ids": STRING_LIST,
                "rejected_argument_ids": STRING_LIST,
                "decision_impact": NON_EMPTY_STRING,
            },
        },
        "status": {"enum": ["resolved", "unresolved", "needs_more_evidence"]},
        "created_at": {"type": "string", "format": "date-time"},
    },
}

R4_DECISION_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_R4_DECISION_SCHEMA,
    "type": "object",
    "additionalProperties": True,
    "required": [
        "schema_version",
        "report_id",
        "workflow_run_id",
        "deal_id",
        "agent_id",
        "research_identity",
        "evidence_snapshot_hash",
        "recommendation",
        "claims",
        "background_knowledge_refs",
        "methodology_refs",
        "startup_receipt_id",
        "startup_retrieval_gate",
        "six_dimension_scorecard",
        "weighted_agent_score",
        "chairman_dimension_score",
        "chairman_qualitative_decision",
        "threshold_result",
        "conditions",
        "monitoring_metrics",
        "decision",
        "score_delta_explanation",
        "executive_summary",
        "decision_rationale",
        "verified_facts",
        "assumptions",
        "core_disputes",
        "principal_risks",
        "valuation_and_exit",
        "generation_mode",
        "revision",
        "parent_report_id",
        "created_at",
    ],
    "properties": {
        "schema_version": {"const": IC_R4_DECISION_SCHEMA},
        "report_id": REPORT_ID_SCHEMA,
        "workflow_run_id": BASE_REPORT_PROPERTIES["workflow_run_id"],
        "deal_id": DEAL_ID_SCHEMA,
        "agent_id": {"const": "siq_ic_chairman"},
        "research_identity": {"type": "object", "minProperties": 1},
        "evidence_snapshot_hash": SNAPSHOT_HASH_SCHEMA,
        "recommendation": {"enum": list(RECOMMENDATIONS)},
        "claims": {"type": "array", "items": IC_CLAIM_JSON_SCHEMA, "minItems": 1},
        "background_knowledge_refs": BASE_REPORT_PROPERTIES["background_knowledge_refs"],
        "methodology_refs": BASE_REPORT_PROPERTIES["methodology_refs"],
        "startup_receipt_id": BASE_REPORT_PROPERTIES["startup_receipt_id"],
        "startup_retrieval_gate": BASE_REPORT_PROPERTIES["startup_retrieval_gate"],
        "six_dimension_scorecard": {"type": "array", "items": SCORECARD_ITEM_SCHEMA, "minItems": 6, "maxItems": 6},
        "weighted_agent_score": {"type": "number", "minimum": 0, "maximum": 100},
        "chairman_dimension_score": {"type": "number", "minimum": 0, "maximum": 100},
        "chairman_qualitative_decision": NON_EMPTY_STRING,
        "threshold_result": {"enum": ["pass", "review", "reject"]},
        "conditions": {"type": "array", "items": {"type": ["object", "string"]}},
        "monitoring_metrics": {"type": "array", "items": {"type": ["object", "string"]}},
        "decision": {"enum": ["pass", "review", "reject", "insufficient_evidence"]},
        "score_delta_explanation": NON_EMPTY_STRING,
        "executive_summary": NON_EMPTY_STRING,
        "decision_rationale": NON_EMPTY_STRING,
        "verified_facts": {"type": "array", "items": {"type": ["object", "string"]}},
        "assumptions": {"type": "array", "items": {"type": ["object", "string"]}},
        "core_disputes": {"type": "array", "items": {"type": ["object", "string"]}},
        "principal_risks": {"type": "array", "items": {"type": ["object", "string"]}},
        "valuation_and_exit": {"type": "array", "items": {"type": ["object", "string"]}},
        "generation_mode": BASE_REPORT_PROPERTIES["generation_mode"],
        "revision": {"type": "integer", "minimum": 1},
        "parent_report_id": BASE_REPORT_PROPERTIES["parent_report_id"],
        "created_at": {"type": "string", "format": "date-time"},
    },
}

REPORT_CONTRACT_SCHEMAS = {
    IC_CLAIM_SCHEMA: IC_CLAIM_JSON_SCHEMA,
    IC_EXPERT_REPORT_SCHEMA: IC_EXPERT_REPORT_JSON_SCHEMA,
    IC_R0_READINESS_SCHEMA: R0_READINESS_JSON_SCHEMA,
    IC_R1_5_DISPUTE_SCHEMA: R1_5_DISPUTE_JSON_SCHEMA,
    IC_R1_5_CHAIRMAN_RULINGS_SCHEMA: R1_5_CHAIRMAN_RULINGS_JSON_SCHEMA,
    IC_R2_REVISION_SCHEMA: R2_REVISION_JSON_SCHEMA,
    IC_R3_PLAN_SCHEMA: R3_PLAN_JSON_SCHEMA,
    IC_R3_DEBATE_SCHEMA: R3_DEBATE_JSON_SCHEMA,
    IC_R3_DEBATE_TURN_SCHEMA: R3_DEBATE_TURN_JSON_SCHEMA,
    IC_R3_DEBATE_VERDICT_SCHEMA: R3_DEBATE_VERDICT_JSON_SCHEMA,
    IC_R4_DECISION_SCHEMA: R4_DECISION_JSON_SCHEMA,
}


def _known_evidence_map(
    known_evidence: Mapping[str, Mapping[str, Any]] | Sequence[str] | set[str] | None,
) -> dict[str, Mapping[str, Any] | None] | None:
    if known_evidence is None:
        return None
    if isinstance(known_evidence, Mapping):
        return {str(key): value for key, value in known_evidence.items()}
    return {str(item): None for item in known_evidence}


def claim_evidence_ids(claim: Mapping[str, Any]) -> set[str]:
    return {
        str(item)
        for field in ("evidence_ids", "counter_evidence_ids")
        for item in claim.get(field, [])
        if str(item or "").strip()
    }


def report_evidence_ids(report: Mapping[str, Any]) -> set[str]:
    return {
        evidence_id
        for claim in report.get("claims", [])
        if isinstance(claim, Mapping)
        for evidence_id in claim_evidence_ids(claim)
    }


def _validate_evidence_identity(
    payload: Mapping[str, Any],
    *,
    known_evidence: Mapping[str, Mapping[str, Any]] | Sequence[str] | set[str] | None,
    expected_deal_id: str | None,
    contract: str,
) -> None:
    evidence_map = _known_evidence_map(known_evidence)
    if evidence_map is None:
        return
    claims = payload.get("claims") if isinstance(payload.get("claims"), list) else []
    evidence_ids = report_evidence_ids(payload)
    errors: list[str] = []
    unknown = sorted(evidence_ids - set(evidence_map))
    if unknown:
        errors.append("unknown_evidence_ids:" + ",".join(unknown))
    for evidence_id in sorted(evidence_ids & set(evidence_map)):
        item = evidence_map[evidence_id]
        if item and expected_deal_id and item.get("deal_id") not in (None, expected_deal_id):
            errors.append(f"cross_deal_evidence_id:{evidence_id}")
    claim_ids = [str(item.get("claim_id")) for item in claims if isinstance(item, Mapping)]
    if len(claim_ids) != len(set(claim_ids)):
        errors.append("duplicate_claim_id")
    combine_validation_errors(contract, errors)


def validate_claim(
    payload: Mapping[str, Any],
    *,
    known_evidence_ids: Sequence[str] | set[str] | None = None,
) -> dict[str, Any]:
    claim = validate_schema(payload, IC_CLAIM_JSON_SCHEMA, contract=IC_CLAIM_SCHEMA)
    errors: list[str] = []
    claim_id = str(claim.get("claim_id") or "unknown")
    status = claim["status"]
    if status == "verified" and not claim["evidence_ids"]:
        errors.append(f"verified_claim_requires_evidence:{claim_id}")
    elif status == "derived":
        if not claim["evidence_ids"]:
            errors.append(f"derived_claim_requires_evidence:{claim_id}")
        if not claim["calculation_trace_ids"]:
            errors.append(f"derived_claim_requires_calculation_trace:{claim_id}")
    elif status == "assumed":
        if not str(claim.get("assumption") or "").strip():
            errors.append(f"assumed_claim_requires_assumption:{claim_id}")
        if not str(claim.get("verification_method") or "").strip():
            errors.append(f"assumed_claim_requires_verification_method:{claim_id}")
    elif status == "contested" and not claim["counter_evidence_ids"]:
        errors.append(f"contested_claim_requires_counter_evidence:{claim_id}")
    if claim["decision_impact"] in {"critical", "material"} and claim["status"] != "missing":
        if not claim["evidence_ids"]:
            errors.append(f"decision_relevant_claim_requires_evidence:{claim_id}")
    if known_evidence_ids is not None:
        unknown = sorted(claim_evidence_ids(claim) - {str(item) for item in known_evidence_ids})
        if unknown:
            errors.append("unknown_evidence_ids:" + ",".join(unknown))
    combine_validation_errors(IC_CLAIM_SCHEMA, errors)
    return claim


def _validate_role_fields(report: Mapping[str, Any]) -> None:
    agent_id = str(report.get("agent_id") or "")
    profile_contract = ic_profile_contract.get_ic_profile_contract(agent_id)
    errors: list[str] = []
    required = ROLE_REQUIRED_FIELDS.get(agent_id)
    if not required or agent_id == "siq_ic_master_coordinator":
        errors.append(f"agent_not_valid_for_expert_report:{agent_id}")
    else:
        for field in required:
            if field not in report or report.get(field) is None:
                errors.append(f"role_field_missing_or_empty:{field}")
                continue
            value = report[field]
            if field not in ROLE_EMPTY_ALLOWED_FIELDS and isinstance(value, (str, list, dict)) and not value:
                errors.append(f"role_field_missing_or_empty:{field}")
    if agent_id == "siq_ic_finance_auditor" and not (
        isinstance(report.get("calculation_trace_ids"), list)
        and bool(report["calculation_trace_ids"])
        and all(str(item or "").strip() for item in report["calculation_trace_ids"])
    ):
        errors.append("finance_calculation_trace_ids_invalid")
    if agent_id == "siq_ic_legal_scanner" and not isinstance(
        report.get("closing_conditions"), (list, dict)
    ):
        errors.append("legal_closing_conditions_not_structured")
    if agent_id == "siq_ic_risk_controller":
        for field in ("warning_thresholds", "stop_loss_thresholds"):
            if not isinstance(report.get(field), (list, dict)):
                errors.append(f"risk_{field}_not_structured")
    if agent_id == "siq_ic_chairman":
        if not isinstance(report.get("six_dimension_scorecard"), list) or len(report["six_dimension_scorecard"]) != 6:
            errors.append("chairman_six_dimension_scorecard_invalid")
        for field in ("weighted_agent_score", "chairman_dimension_score"):
            if isinstance(report.get(field), bool) or not isinstance(report.get(field), (int, float)):
                errors.append(f"chairman_{field}_not_numeric")
        if report.get("decision") not in {"pass", "review", "reject", "insufficient_evidence"}:
            errors.append("chairman_decision_invalid")
    for field in ROLE_FORBIDDEN_FIELDS.get(agent_id, ()):
        if report.get(field) not in (None, "", [], {}):
            errors.append(f"role_boundary_forbidden_field:{field}")
    if agent_id != "siq_ic_chairman":
        for field in ("decision", "chairman_qualitative_decision", "chairman_dimension_score"):
            if report.get(field) not in (None, "", [], {}):
                errors.append(f"role_boundary_forbidden_field:{field}")
    background_refs = [*report.get("background_knowledge_refs", []), *report.get("methodology_refs", [])]
    ref_ids = [item["ref_id"] for item in background_refs if isinstance(item, Mapping)]
    if len(ref_ids) != len(set(ref_ids)):
        errors.append("duplicate_background_knowledge_ref_id")
    expected_private_collection = profile_contract.get("private_knowledge_collection")
    if any(
        item.get("collection") != expected_private_collection
        for item in background_refs
        if isinstance(item, Mapping)
    ):
        errors.append("background_knowledge_collection_not_owned_by_agent")
    registered = set(ref_ids)
    for claim in report.get("claims", []):
        if not isinstance(claim, Mapping):
            continue
        referenced = set(claim.get("background_knowledge_ref_ids", [])) | set(claim.get("methodology_ref_ids", []))
        unknown = referenced - registered
        if unknown:
            errors.append("claim_unknown_background_knowledge_refs:" + ",".join(sorted(unknown)))
    gate = report.get("startup_retrieval_gate") or {}
    retrieval = profile_contract.get("retrieval") or {}
    if retrieval.get("required") and not (
        gate.get("allowed_to_speak")
        and gate.get("project_evidence_ready")
        and gate.get("private_background_ready")
        and not gate.get("blocking_reasons")
    ):
        errors.append("startup_retrieval_gate_not_ready")
    if gate.get("private_collection") != retrieval.get("private_collection"):
        errors.append("startup_retrieval_private_collection_mismatch")
    if gate.get("shared_collection") != retrieval.get("shared_collection"):
        errors.append("startup_retrieval_shared_collection_mismatch")
    combine_validation_errors(IC_EXPERT_REPORT_SCHEMA, errors)


def _validate_report_phase(report: Mapping[str, Any]) -> None:
    agent_id = str(report["agent_id"])
    phase = str(report["phase"])
    allowed = {
        "R1A": {
            "siq_ic_strategist",
            "siq_ic_sector_expert",
            "siq_ic_finance_auditor",
            "siq_ic_legal_scanner",
        },
        "R1B": {"siq_ic_risk_controller", "siq_ic_chairman"},
        "R2": {
            "siq_ic_strategist",
            "siq_ic_sector_expert",
            "siq_ic_finance_auditor",
            "siq_ic_legal_scanner",
            "siq_ic_risk_controller",
        },
    }
    combine_validation_errors(
        IC_EXPERT_REPORT_SCHEMA,
        [] if agent_id in allowed.get(phase, set()) else [f"agent_phase_not_allowed:{agent_id}:{phase}"],
    )


def validate_expert_report(
    payload: Mapping[str, Any],
    *,
    expected_deal_id: str | None = None,
    expected_agent_id: str | None = None,
    expected_snapshot_hash: str | None = None,
    known_evidence: Mapping[str, Mapping[str, Any]] | Sequence[str] | set[str] | None = None,
) -> dict[str, Any]:
    report = validate_schema(payload, IC_EXPERT_REPORT_JSON_SCHEMA, contract=IC_EXPERT_REPORT_SCHEMA)
    require_identity(
        report,
        contract=IC_EXPERT_REPORT_SCHEMA,
        expected_deal_id=expected_deal_id,
        expected_agent_id=expected_agent_id,
        expected_snapshot_hash=expected_snapshot_hash,
    )
    _validate_report_phase(report)
    _validate_role_fields(report)
    known_ids = None if known_evidence is None else set(_known_evidence_map(known_evidence) or ())
    for claim in report["claims"]:
        validate_claim(claim, known_evidence_ids=known_ids)
    _validate_evidence_identity(
        report,
        known_evidence=known_evidence,
        expected_deal_id=expected_deal_id or str(report["deal_id"]),
        contract=IC_EXPERT_REPORT_SCHEMA,
    )
    claim_ids = {claim["claim_id"] for claim in report["claims"]}
    scorecard_claim_ids = {
        claim_id
        for item in report["scorecard"]
        for claim_id in item.get("claim_ids", [])
    }
    combine_validation_errors(
        IC_EXPERT_REPORT_SCHEMA,
        [
            "scorecard_unknown_claim_ids:" + ",".join(sorted(scorecard_claim_ids - claim_ids))
        ]
        if scorecard_claim_ids - claim_ids
        else [],
    )
    return report


def validate_r0_readiness(payload: Mapping[str, Any], **identity: Any) -> dict[str, Any]:
    artifact = validate_schema(payload, R0_READINESS_JSON_SCHEMA, contract=IC_R0_READINESS_SCHEMA)
    require_identity(artifact, contract=IC_R0_READINESS_SCHEMA, **identity)
    if artifact["readiness"] == "ready" and artifact["blocking_reasons"]:
        raise ICContractValidationError(IC_R0_READINESS_SCHEMA, ["ready_with_blocking_reasons"])
    return artifact


def validate_r1_5_dispute(
    payload: Mapping[str, Any],
    *,
    known_evidence_ids: Sequence[str] | set[str] | None = None,
    **identity: Any,
) -> dict[str, Any]:
    artifact = validate_schema(payload, R1_5_DISPUTE_JSON_SCHEMA, contract=IC_R1_5_DISPUTE_SCHEMA)
    require_identity(artifact, contract=IC_R1_5_DISPUTE_SCHEMA, **identity)
    errors: list[str] = []
    if artifact["ruling"] == "needs_more_evidence" and not artifact["required_followups"]:
        errors.append("needs_more_evidence_requires_followups")
    if artifact["ruling"] not in {"needs_more_evidence", "unresolved"}:
        if artifact["severity"] in {"critical", "high"} and not artifact["evidence_ids"]:
            errors.append("high_severity_resolved_ruling_requires_evidence")
        if len(artifact["positions"]) < 2:
            errors.append("resolved_ruling_requires_two_positions")
    if known_evidence_ids is not None:
        referenced = set(artifact["evidence_ids"]) | set(artifact["counter_evidence_ids"])
        unknown = sorted(referenced - {str(item) for item in known_evidence_ids})
        if unknown:
            errors.append("unknown_evidence_ids:" + ",".join(unknown))
    combine_validation_errors(IC_R1_5_DISPUTE_SCHEMA, errors)
    return artifact


def validate_r2_revision(
    payload: Mapping[str, Any],
    *,
    known_evidence: Mapping[str, Mapping[str, Any]] | Sequence[str] | set[str] | None = None,
    **identity: Any,
) -> dict[str, Any]:
    artifact = validate_schema(payload, R2_REVISION_JSON_SCHEMA, contract=IC_R2_REVISION_SCHEMA)
    report = validate_expert_report(artifact["report"], known_evidence=known_evidence, **identity)
    errors: list[str] = []
    expected_delta = round(float(artifact["r2_score"]) - float(artifact["r1_score"]), 6)
    if abs(float(artifact["score_change"]) - expected_delta) > 1e-6:
        errors.append("score_change_mismatch")
    if abs(float(report["score"]) - float(artifact["r2_score"])) > 1e-6:
        errors.append("r2_report_score_mismatch")
    claim_ids = {claim["claim_id"] for claim in report["claims"]}
    referenced_claims = set(artifact["changed_claims"]) | set(artifact["unchanged_claims"])
    if referenced_claims != claim_ids:
        errors.append("revision_claim_partition_mismatch")
    if not artifact["changed_claims"] and not artifact["revision_rationale"].strip():
        errors.append("unchanged_revision_requires_rationale")
    combine_validation_errors(IC_R2_REVISION_SCHEMA, errors)
    return artifact


def validate_r3_plan(payload: Mapping[str, Any]) -> dict[str, Any]:
    artifact = validate_schema(payload, R3_PLAN_JSON_SCHEMA, contract=IC_R3_PLAN_SCHEMA)
    errors = []
    if artifact["mode"] == "skip":
        if artifact["estimated_rounds"] != 0:
            errors.append("skip_estimated_rounds_must_be_zero")
        checks = artifact.get("skip_checks")
        if not isinstance(checks, dict) or not checks or not all(checks.values()):
            errors.append("skip_safety_checks_not_satisfied")
        if artifact["requires_human_confirmation_to_skip"] and artifact.get("human_skip_confirmation") is not True:
            errors.append("skip_human_confirmation_missing")
    elif artifact["estimated_rounds"] <= 0 or not artifact["topics"]:
        errors.append("debate_mode_requires_topics_and_rounds")
    combine_validation_errors(IC_R3_PLAN_SCHEMA, errors)
    return artifact


def validate_r3_debate(
    payload: Mapping[str, Any],
    *,
    known_evidence_ids: Sequence[str] | set[str] | None = None,
    **identity: Any,
) -> dict[str, Any]:
    artifact = validate_schema(payload, R3_DEBATE_JSON_SCHEMA, contract=IC_R3_DEBATE_SCHEMA)
    require_identity(artifact, contract=IC_R3_DEBATE_SCHEMA, **identity)
    errors: list[str] = []
    if set(artifact["red_team"]) & set(artifact["blue_team"]):
        errors.append("debate_team_overlap")
    argument_ids = [turn["argument_id"] for turn in artifact["rounds"]]
    if len(argument_ids) != len(set(argument_ids)):
        errors.append("duplicate_argument_id")
    known_arguments: set[str] = set()
    for turn in artifact["rounds"]:
        if not set(turn["responds_to_argument_ids"]).issubset(known_arguments):
            errors.append(f"responds_to_unknown_or_future_argument:{turn['argument_id']}")
        known_arguments.add(turn["argument_id"])
    if known_evidence_ids is not None:
        referenced = {item for turn in artifact["rounds"] for item in turn["evidence_ids"]}
        unknown = sorted(referenced - {str(item) for item in known_evidence_ids})
        if unknown:
            errors.append("unknown_evidence_ids:" + ",".join(unknown))
    combine_validation_errors(IC_R3_DEBATE_SCHEMA, errors)
    return artifact


def validate_r4_decision(
    payload: Mapping[str, Any],
    *,
    expected_deal_id: str | None = None,
    expected_snapshot_hash: str | None = None,
    known_evidence: Mapping[str, Mapping[str, Any]] | Sequence[str] | set[str] | None = None,
) -> dict[str, Any]:
    artifact = validate_schema(payload, R4_DECISION_JSON_SCHEMA, contract=IC_R4_DECISION_SCHEMA)
    require_identity(
        artifact,
        contract=IC_R4_DECISION_SCHEMA,
        expected_deal_id=expected_deal_id,
        expected_agent_id="siq_ic_chairman",
        expected_snapshot_hash=expected_snapshot_hash,
    )
    known_ids = None if known_evidence is None else set(_known_evidence_map(known_evidence) or ())
    for claim in artifact["claims"]:
        validate_claim(claim, known_evidence_ids=known_ids)
    chairman_profile = ic_profile_contract.get_ic_profile_contract("siq_ic_chairman")
    knowledge_refs = [*artifact["background_knowledge_refs"], *artifact["methodology_refs"]]
    registered_refs = {item["ref_id"] for item in knowledge_refs}
    knowledge_errors: list[str] = []
    expected_private = chairman_profile.get("private_knowledge_collection")
    if any(item.get("collection") != expected_private for item in knowledge_refs):
        knowledge_errors.append("background_knowledge_collection_not_owned_by_agent")
    if len(registered_refs) != len(knowledge_refs):
        knowledge_errors.append("duplicate_background_knowledge_ref_id")
    for claim in artifact["claims"]:
        unknown_refs = (
            set(claim.get("background_knowledge_ref_ids", []))
            | set(claim.get("methodology_ref_ids", []))
        ) - registered_refs
        if unknown_refs:
            knowledge_errors.append(
                "claim_unknown_background_knowledge_refs:" + ",".join(sorted(unknown_refs))
            )
    gate = artifact["startup_retrieval_gate"]
    retrieval = chairman_profile.get("retrieval") or {}
    if not (
        gate.get("allowed_to_speak")
        and gate.get("project_evidence_ready")
        and gate.get("private_background_ready")
        and not gate.get("blocking_reasons")
    ):
        knowledge_errors.append("startup_retrieval_gate_not_ready")
    if gate.get("private_collection") != retrieval.get("private_collection"):
        knowledge_errors.append("startup_retrieval_private_collection_mismatch")
    if gate.get("shared_collection") != retrieval.get("shared_collection"):
        knowledge_errors.append("startup_retrieval_shared_collection_mismatch")
    _validate_evidence_identity(
        artifact,
        known_evidence=known_evidence,
        expected_deal_id=expected_deal_id or str(artifact["deal_id"]),
        contract=IC_R4_DECISION_SCHEMA,
    )
    errors: list[str] = list(knowledge_errors)
    dimensions = artifact["six_dimension_scorecard"]
    claim_ids = {str(item["claim_id"]) for item in artifact["claims"]}
    scorecard_claim_ids = {
        str(claim_id)
        for dimension in dimensions
        for claim_id in dimension.get("claim_ids", [])
    }
    unknown_scorecard_claim_ids = sorted(scorecard_claim_ids - claim_ids)
    if unknown_scorecard_claim_ids:
        errors.append(
            "six_dimension_scorecard_unknown_claim_ids:"
            + ",".join(unknown_scorecard_claim_ids)
        )
    if len({item["dimension"] for item in dimensions}) != 6:
        errors.append("six_dimension_scorecard_dimensions_not_unique")
    weight_sum = sum(float(item["weight"]) for item in dimensions)
    scale = 100.0 if weight_sum > 1.5 else 1.0
    if abs(weight_sum - scale) > 0.01:
        errors.append("six_dimension_weights_do_not_sum_to_scale")
    computed = sum(float(item["score"]) * float(item["weight"]) / scale for item in dimensions)
    if abs(computed - float(artifact["chairman_dimension_score"])) > 0.11:
        errors.append("chairman_dimension_score_mismatch")
    if artifact["decision"] == "pass" and artifact["recommendation"] not in {"support", "conditional_support"}:
        errors.append("decision_recommendation_mismatch")
    combine_validation_errors(IC_R4_DECISION_SCHEMA, errors)
    return artifact


def validate_phase_artifact(
    payload: Mapping[str, Any],
    **context: Any,
) -> dict[str, Any]:
    schema_version = str(payload.get("schema_version") or "")
    if schema_version == IC_EXPERT_REPORT_SCHEMA:
        return validate_expert_report(payload, **context)
    if schema_version == IC_R0_READINESS_SCHEMA:
        return validate_r0_readiness(payload, **context)
    if schema_version == IC_R1_5_DISPUTE_SCHEMA:
        return validate_r1_5_dispute(payload, **context)
    if schema_version == IC_R2_REVISION_SCHEMA:
        return validate_r2_revision(payload, **context)
    if schema_version == IC_R3_PLAN_SCHEMA:
        return validate_r3_plan(payload)
    if schema_version == IC_R3_DEBATE_SCHEMA:
        return validate_r3_debate(payload, **context)
    if schema_version == IC_R4_DECISION_SCHEMA:
        return validate_r4_decision(payload, **context)
    raise ValueError(f"Unknown IC report contract schema: {schema_version}")


def get_report_contract_schema(schema_version: str) -> dict[str, Any]:
    try:
        return deepcopy(REPORT_CONTRACT_SCHEMAS[schema_version])
    except KeyError as exc:
        raise ValueError(f"Unknown IC report contract schema: {schema_version}") from exc


def profile_output_contracts() -> dict[str, list[str]]:
    return {profile_id: list(ROLE_OUTPUT_SCHEMAS[profile_id]) for profile_id in ic_policy.IC_PROFILE_IDS}


__all__ = [
    "ICContractValidationError",
    "IC_CLAIM_SCHEMA",
    "IC_EXPERT_REPORT_SCHEMA",
    "IC_R0_READINESS_SCHEMA",
    "IC_R1_5_CHAIRMAN_RULINGS_SCHEMA",
    "IC_R1_5_DISPUTE_SCHEMA",
    "IC_R2_REVISION_SCHEMA",
    "IC_R3_DEBATE_SCHEMA",
    "IC_R3_DEBATE_TURN_SCHEMA",
    "IC_R3_DEBATE_VERDICT_SCHEMA",
    "IC_R3_PLAN_SCHEMA",
    "IC_R4_DECISION_SCHEMA",
    "ROLE_REQUIRED_FIELDS",
    "get_report_contract_schema",
    "profile_output_contracts",
    "report_evidence_ids",
    "validate_claim",
    "validate_expert_report",
    "validate_phase_artifact",
    "validate_r0_readiness",
    "validate_r1_5_dispute",
    "validate_r2_revision",
    "validate_r3_debate",
    "validate_r3_plan",
    "validate_r4_decision",
]
