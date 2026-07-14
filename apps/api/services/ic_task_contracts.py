"""Versioned task, handoff, and workflow identity contracts for IC phases."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from services import ic_policy, ic_profile_contract
from services.ic_contract_validation import (
    ICContractValidationError,
    combine_validation_errors,
    require_identity,
    validate_schema,
)

IC_AGENT_TASK_SCHEMA = "siq_ic_agent_task_v2"
IC_AGENT_HANDOFF_V1_SCHEMA = "siq_ic_agent_handoff_v1"
IC_AGENT_HANDOFF_SCHEMA = "siq_ic_agent_handoff_v2"
IC_WORKFLOW_RUN_IDENTITY_SCHEMA = "siq_ic_workflow_run_identity_v1"
IC_WORKFLOW_RUN_SCHEMA = "siq_ic_workflow_run_v1"

IC_PHASES = ("R0", "R1A", "R1B", "R1.5", "R2", "R3", "R4")
IC_TASK_TERMINAL_STATES = (
    "succeeded",
    "failed",
    "cancelled",
    "interrupted",
    "timed_out",
    "stale_on_completion",
)

ROLE_PHASE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "siq_ic_master_coordinator": ("R0", "R4"),
    "siq_ic_strategist": ("R1A", "R2", "R3"),
    "siq_ic_sector_expert": ("R1A", "R2", "R3"),
    "siq_ic_finance_auditor": ("R1A", "R2", "R3"),
    "siq_ic_legal_scanner": ("R1A", "R2", "R3"),
    "siq_ic_risk_controller": ("R1B", "R2", "R3"),
    "siq_ic_chairman": ("R1B", "R1.5", "R3", "R4"),
}

ROLE_OUTPUT_SCHEMAS: dict[str, tuple[str, ...]] = {
    "siq_ic_master_coordinator": (
        "siq_ic_r0_readiness_v1",
        IC_AGENT_HANDOFF_SCHEMA,
        "siq_ic_r4_report_view_model_v1",
    ),
    "siq_ic_strategist": ("siq_ic_expert_report_v2", "siq_ic_r2_revision_v1", "siq_ic_r3_debate_v1"),
    "siq_ic_sector_expert": ("siq_ic_expert_report_v2", "siq_ic_r2_revision_v1", "siq_ic_r3_debate_v1"),
    "siq_ic_finance_auditor": ("siq_ic_expert_report_v2", "siq_ic_r2_revision_v1", "siq_ic_r3_debate_v1"),
    "siq_ic_legal_scanner": ("siq_ic_expert_report_v2", "siq_ic_r2_revision_v1", "siq_ic_r3_debate_v1"),
    "siq_ic_risk_controller": ("siq_ic_expert_report_v2", "siq_ic_r2_revision_v1", "siq_ic_r3_debate_v1"),
    "siq_ic_chairman": (
        "siq_ic_expert_report_v2",
        "siq_ic_r1_5_dispute_v1",
        "siq_ic_r3_debate_v1",
        "siq_ic_r4_decision_v2",
    ),
}
RUNTIME_OUTPUT_SCHEMAS_BY_PHASE: dict[str, tuple[str, ...]] = {
    "R0": ("siq_ic_r0_readiness_v1",),
    "R1A": ("siq_ic_agent_report_v2", "siq_ic_expert_report_v2"),
    "R1B": ("siq_ic_agent_report_v2", "siq_ic_expert_report_v2"),
    "R1.5": ("siq_ic_r1_5_chairman_rulings_v2", "siq_ic_r1_5_dispute_v1"),
    "R2": ("siq_ic_r2_revision_report_v2", "siq_ic_r2_revision_v1"),
    "R3": (
        "siq_ic_r3_debate_turn_v1",
        "siq_ic_r3_debate_verdict_v1",
        "siq_ic_r3_debate_v1",
    ),
    "R4": ("siq_ic_r4_decision_v2", "siq_ic_r4_report_view_model_v1"),
}

ID_DEFINITIONS = {
    "task_id": {"type": "string", "pattern": r"^ICTASK-[A-Za-z0-9_.-]{8,160}$"},
    "workflow_run_id": {"type": "string", "pattern": r"^ICRUN-[A-Z0-9][A-Z0-9-]{7,95}$"},
    "handoff_id": {"type": "string", "pattern": r"^ICHANDOFF-[A-Z0-9][A-Z0-9-]{7,95}$"},
    "deal_id": {"type": "string", "pattern": r"^[A-Z0-9][A-Z0-9_-]{2,96}$"},
    "snapshot_hash": {"type": "string", "pattern": r"^[a-fA-F0-9]{64}$"},
    "digest": {"type": "string", "pattern": r"^[a-fA-F0-9]{64}$"},
    "agent_id": {"type": "string", "enum": list(ic_policy.IC_PROFILE_IDS)},
    "phase": {"type": "string", "enum": list(IC_PHASES)},
}

STRING_LIST_SCHEMA = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
    "uniqueItems": True,
}
RUNTIME_LABEL_SCHEMA = {
    "anyOf": [
        {
            "type": "string",
            "pattern": r"^(?!.*://)[A-Za-z0-9][A-Za-z0-9._:/+-]{0,159}$",
        },
        {"type": "null"},
    ]
}
RUN_RUNTIME_METADATA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "requested_model",
        "configured",
        "effective",
        "fallback",
    ],
    "properties": {
        "schema_version": {"const": "hermes.run_runtime.v1"},
        "requested_model": RUNTIME_LABEL_SCHEMA,
        "configured": {
            "type": "object",
            "additionalProperties": False,
            "required": ["provider", "model"],
            "properties": {
                "provider": RUNTIME_LABEL_SCHEMA,
                "model": RUNTIME_LABEL_SCHEMA,
            },
        },
        "effective": {
            "type": "object",
            "additionalProperties": False,
            "required": ["provider", "model"],
            "properties": {
                "provider": RUNTIME_LABEL_SCHEMA,
                "model": RUNTIME_LABEL_SCHEMA,
            },
        },
        "fallback": {
            "type": "object",
            "additionalProperties": False,
            "required": ["activated"],
            "properties": {"activated": {"type": ["boolean", "null"]}},
        },
    },
}
BACKGROUND_REF_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ref_id", "collection", "locator", "title", "usage"],
    "properties": {
        "ref_id": {"type": "string", "pattern": r"^KBREF-[A-Z0-9][A-Z0-9-]{5,95}$"},
        "collection": {"type": "string", "minLength": 1, "maxLength": 128},
        "locator": {"type": "string", "minLength": 1, "maxLength": 500},
        "title": {"type": "string", "minLength": 1, "maxLength": 500},
        "usage": {"enum": ["background", "methodology", "comparable_context"]},
    },
}
STARTUP_RETRIEVAL_GATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "receipt_id",
        "allowed_to_speak",
        "project_evidence_ready",
        "private_background_ready",
        "shared_collection",
        "private_collection",
        "blocking_reasons",
    ],
    "properties": {
        "receipt_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "allowed_to_speak": {"type": "boolean"},
        "project_evidence_ready": {"type": "boolean"},
        "private_background_ready": {"type": "boolean"},
        "shared_collection": {"type": "string", "minLength": 1, "maxLength": 128},
        "private_collection": {"type": "string", "minLength": 1, "maxLength": 128},
        "blocking_reasons": STRING_LIST_SCHEMA,
    },
}
KNOWLEDGE_CONTEXT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "agent_id",
        "phase",
        "status",
        "degraded_reasons",
        "receipt_id",
        "retrieval_status",
        "milvus_used",
        "shared_collections",
        "private_collections",
        "physical_collections",
        "project_evidence_hits",
        "shared_background_hits",
        "private_background_hits",
        "rules",
        "digest",
    ],
    "properties": {
        "schema_version": {"const": "siq_ic_knowledge_context_v1"},
        "agent_id": ID_DEFINITIONS["agent_id"],
        "phase": ID_DEFINITIONS["phase"],
        "status": {"enum": ["current", "degraded"]},
        "degraded_reasons": STRING_LIST_SCHEMA,
        "receipt_id": {"type": "string", "minLength": 1, "maxLength": 160},
        "receipt_round_name": {"type": ["string", "null"], "maxLength": 64},
        "retrieval_status": {"type": "string", "minLength": 1, "maxLength": 64},
        "milvus_used": {"type": "boolean"},
        "shared_collections": STRING_LIST_SCHEMA,
        "private_collections": STRING_LIST_SCHEMA,
        "physical_collections": {"type": ["object", "array"]},
        "project_evidence_hits": {"type": "array", "items": {"type": "object"}},
        "shared_background_hits": {"type": "array", "items": {"type": "object"}},
        "private_background_hits": {"type": "array", "items": {"type": "object"}},
        "rules": {**STRING_LIST_SCHEMA, "minItems": 1},
        "digest": ID_DEFINITIONS["digest"],
    },
}

IC_AGENT_TASK_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_AGENT_TASK_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "task_id",
        "workflow_run_id",
        "deal_id",
        "phase",
        "round_name",
        "agent_id",
        "research_identity",
        "evidence_snapshot_hash",
        "prompt_contract_version",
        "profile_contract_version",
        "input_artifacts",
        "background_knowledge_refs",
        "methodology_refs",
        "startup_retrieval_gate",
        "input_digest",
        "role_objectives",
        "required_questions",
        "hard_rules",
        "output_schema",
        "timeout_seconds",
        "created_at",
    ],
    "properties": {
        "schema_version": {"const": IC_AGENT_TASK_SCHEMA},
        "task_id": ID_DEFINITIONS["task_id"],
        "workflow_run_id": ID_DEFINITIONS["workflow_run_id"],
        "deal_id": ID_DEFINITIONS["deal_id"],
        "phase": ID_DEFINITIONS["phase"],
        "round_name": {"type": "string", "minLength": 1, "maxLength": 64},
        "agent_id": ID_DEFINITIONS["agent_id"],
        "research_identity": {"type": "object", "minProperties": 1},
        "evidence_snapshot_hash": ID_DEFINITIONS["snapshot_hash"],
        "prompt_contract_version": {"type": "string", "minLength": 1, "maxLength": 100},
        "profile_contract_version": {"type": "string", "minLength": 1, "maxLength": 100},
        "input_artifacts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["artifact_id", "artifact_type", "sha256"],
                "properties": {
                    "artifact_id": {"type": "string", "minLength": 1, "maxLength": 160},
                    "artifact_type": {"type": "string", "minLength": 1, "maxLength": 100},
                    "sha256": ID_DEFINITIONS["digest"],
                    "report_id": {"type": "string", "pattern": r"^ICRPT-[A-Z0-9][A-Z0-9-]{7,95}$"},
                },
            },
        },
        "background_knowledge_refs": {"type": "array", "items": BACKGROUND_REF_SCHEMA},
        "methodology_refs": {"type": "array", "items": BACKGROUND_REF_SCHEMA},
        "startup_retrieval_gate": STARTUP_RETRIEVAL_GATE_SCHEMA,
        "input_digest": ID_DEFINITIONS["digest"],
        "role_objectives": {**STRING_LIST_SCHEMA, "minItems": 1},
        "required_questions": {**STRING_LIST_SCHEMA, "minItems": 1},
        "hard_rules": {**STRING_LIST_SCHEMA, "minItems": 1},
        "output_schema": {"type": "string", "minLength": 1, "maxLength": 100},
        "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 14400},
        "updated_at": {"type": "string", "format": "date-time"},
        "status": {
            "enum": [
                "queued",
                "running",
                "succeeded",
                "failed",
                "cancelled",
                "interrupted",
                "timed_out",
                "stale_on_completion",
            ]
        },
        "generation_mode": {"enum": ["hermes_model", "hermes_model_degraded"]},
        "handoff_id": {"anyOf": [ID_DEFINITIONS["handoff_id"], {"type": "null"}]},
        "handoff_digest": {"anyOf": [ID_DEFINITIONS["digest"], {"type": "null"}]},
        "task_claim": {"type": "object"},
        "started_at": {"type": "string", "format": "date-time"},
        "hermes_called": {"type": "boolean"},
        "hermes_run_id": {"type": ["string", "null"]},
        "hermes_run_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
        "output_artifact_path": {"type": "string"},
        "output_artifact_paths": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
        "output_artifact_hash": ID_DEFINITIONS["digest"],
        "output_artifact_hashes": {
            "type": "object",
            "additionalProperties": ID_DEFINITIONS["digest"],
        },
        "contract_validation": {
            "type": "object",
            "additionalProperties": False,
            "required": ["passed", "output_schema"],
            "properties": {
                "passed": {"type": "boolean"},
                "output_schema": {"type": "string", "minLength": 1, "maxLength": 100},
                "artifact_schema": {"type": ["string", "null"], "maxLength": 100},
                "validated_by": {"type": "string", "minLength": 1, "maxLength": 100},
                "error_type": {"type": "string", "minLength": 1, "maxLength": 160},
            },
        },
        "model_execution_audit": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version",
                "runtime_metadata_status",
                "attempt_count",
                "attempts",
                "final_hermes_run_id",
                "final_prompt_sha256",
                "final_runtime",
            ],
            "properties": {
                "schema_version": {"const": "siq_ic_model_execution_audit_v1"},
                "runtime_metadata_status": {"enum": ["verified", "unverified"]},
                "attempt_count": {"type": "integer", "minimum": 0},
                "attempts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "hermes_run_id",
                            "purpose",
                            "prompt_sha256",
                            "terminal_status",
                            "runtime_metadata_status",
                            "runtime",
                        ],
                        "properties": {
                            "hermes_run_id": {"type": "string", "minLength": 1},
                            "purpose": {"enum": ["generation", "contract_repair"]},
                            "prompt_sha256": ID_DEFINITIONS["digest"],
                            "terminal_status": {
                                "enum": [
                                    "succeeded",
                                    "failed",
                                    "cancelled",
                                    "timed_out",
                                    "protocol_eof",
                                    "unavailable",
                                ]
                            },
                            "runtime_metadata_status": {"enum": ["verified", "unverified"]},
                            "runtime": {
                                "anyOf": [RUN_RUNTIME_METADATA_SCHEMA, {"type": "null"}],
                            },
                        },
                    },
                },
                "final_hermes_run_id": {"type": ["string", "null"]},
                "final_prompt_sha256": {
                    "anyOf": [ID_DEFINITIONS["digest"], {"type": "null"}]
                },
                "final_runtime": {
                    "anyOf": [RUN_RUNTIME_METADATA_SCHEMA, {"type": "null"}],
                },
            },
        },
        "attempt_history": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "lease_attempt",
                    "terminal_status",
                    "started_at",
                    "terminal_at",
                    "hermes_run_id",
                    "hermes_run_ids",
                    "output_artifact_path",
                    "output_artifact_paths",
                    "output_artifact_hash",
                    "output_artifact_hashes",
                    "contract_validation",
                    "error",
                ],
                "properties": {
                    "lease_attempt": {"type": "integer", "minimum": 1},
                    "terminal_status": {
                        "enum": [
                            "succeeded",
                            "failed",
                            "cancelled",
                            "interrupted",
                            "timed_out",
                            "stale_on_completion",
                        ]
                    },
                    "started_at": {"type": ["string", "null"], "format": "date-time"},
                    "terminal_at": {"type": ["string", "null"], "format": "date-time"},
                    "hermes_run_id": {"type": ["string", "null"]},
                    "hermes_run_ids": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                    },
                    "output_artifact_path": {"type": ["string", "null"]},
                    "output_artifact_paths": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 1},
                        "uniqueItems": True,
                    },
                    "output_artifact_hash": {
                        "anyOf": [ID_DEFINITIONS["digest"], {"type": "null"}]
                    },
                    "output_artifact_hashes": {
                        "type": "object",
                        "additionalProperties": ID_DEFINITIONS["digest"],
                    },
                    "contract_validation": {"type": "object"},
                    "model_execution_audit": {"type": "object"},
                    "error": {"type": ["string", "null"], "maxLength": 500},
                },
            },
        },
        "validated_output": {"type": "object"},
        "r4_decision_identity": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version",
                "task_id",
                "workflow_run_id",
                "deal_id",
                "evidence_snapshot_hash",
                "report_id",
                "revision",
                "input_digest",
                "handoff_digest",
                "hermes_run_id",
                "created_at",
                "updated_at",
                "decision_sha256",
            ],
            "properties": {
                "schema_version": {"const": "siq_ic_r4_decision_identity_v1"},
                "task_id": ID_DEFINITIONS["task_id"],
                "workflow_run_id": ID_DEFINITIONS["workflow_run_id"],
                "deal_id": ID_DEFINITIONS["deal_id"],
                "evidence_snapshot_hash": ID_DEFINITIONS["snapshot_hash"],
                "report_id": {
                    "type": "string",
                    "pattern": r"^ICRPT-[A-Z0-9][A-Z0-9-]{7,95}$",
                },
                "revision": {"type": "integer", "minimum": 1},
                "input_digest": ID_DEFINITIONS["digest"],
                "handoff_digest": ID_DEFINITIONS["digest"],
                "hermes_run_id": {"type": "string", "minLength": 1},
                "created_at": {"type": "string", "format": "date-time"},
                "updated_at": {"type": "string", "format": "date-time"},
                "decision_sha256": ID_DEFINITIONS["digest"],
            },
        },
        "stale_on_completion": {"type": "boolean"},
        "current_evidence_snapshot_hash": {
            "anyOf": [ID_DEFINITIONS["snapshot_hash"], {"type": "null"}]
        },
        "completed_at": {"type": "string", "format": "date-time"},
        "failure_reason": {"type": "string", "maxLength": 500},
        "created_at": {"type": "string", "format": "date-time"},
    },
}

IC_AGENT_HANDOFF_V1_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_AGENT_HANDOFF_V1_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "handoff_id",
        "workflow_run_id",
        "deal_id",
        "phase",
        "from_agent_id",
        "to_agent_id",
        "source_report_ids",
        "claim_ids",
        "dispute_ids",
        "evidence_ids",
        "evidence_snapshot_hash",
        "input_digest",
        "created_at",
    ],
    "properties": {
        "schema_version": {"const": IC_AGENT_HANDOFF_V1_SCHEMA},
        "handoff_id": ID_DEFINITIONS["handoff_id"],
        "workflow_run_id": ID_DEFINITIONS["workflow_run_id"],
        "deal_id": ID_DEFINITIONS["deal_id"],
        "phase": ID_DEFINITIONS["phase"],
        "from_agent_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "to_agent_id": ID_DEFINITIONS["agent_id"],
        "source_report_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 180},
            "uniqueItems": True,
        },
        "claim_ids": {
            "type": "array",
            "items": {"type": "string", "pattern": r"^CLM-[A-Z0-9][A-Z0-9-]{5,95}$"},
            "uniqueItems": True,
        },
        "dispute_ids": {
            "type": "array",
            "items": {"type": "string", "pattern": r"^DSP-[A-Z0-9][A-Z0-9-]{5,95}$"},
            "uniqueItems": True,
        },
        "evidence_ids": {
            "type": "array",
            "items": {"type": "string", "pattern": r"^EVID-[A-Za-z0-9][A-Za-z0-9:_-]{2,190}$"},
            "uniqueItems": True,
        },
        "evidence_snapshot_hash": ID_DEFINITIONS["snapshot_hash"],
        "input_digest": ID_DEFINITIONS["digest"],
        "created_at": {"type": "string", "format": "date-time"},
    },
}

IC_AGENT_HANDOFF_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_AGENT_HANDOFF_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "handoff_id",
        "workflow_run_id",
        "deal_id",
        "phase",
        "from_agent_id",
        "to_agent_id",
        "source_report_ids",
        "claim_ids",
        "dispute_ids",
        "project_evidence_ids",
        "source_ids",
        "reports",
        "payload",
        "background_knowledge",
        "sidecar_digest",
        "evidence_snapshot_hash",
        "input_digest",
        "created_at",
    ],
    "properties": {
        "schema_version": {"const": IC_AGENT_HANDOFF_SCHEMA},
        "handoff_id": ID_DEFINITIONS["handoff_id"],
        "workflow_run_id": ID_DEFINITIONS["workflow_run_id"],
        "deal_id": ID_DEFINITIONS["deal_id"],
        "phase": ID_DEFINITIONS["phase"],
        "from_agent_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "to_agent_id": ID_DEFINITIONS["agent_id"],
        "source_report_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 180},
            "uniqueItems": True,
        },
        "claim_ids": {
            "type": "array",
            "items": {"type": "string", "pattern": r"^CLM-[A-Z0-9][A-Z0-9-]{5,95}$"},
            "uniqueItems": True,
        },
        "dispute_ids": {
            "type": "array",
            "items": {"type": "string", "pattern": r"^DISP-[A-Z0-9][A-Z0-9-]{5,95}$"},
            "uniqueItems": True,
        },
        "project_evidence_ids": {
            "type": "array",
            "items": {"type": "string", "pattern": r"^EVID-[A-Za-z0-9][A-Za-z0-9:_-]{2,190}$"},
            "uniqueItems": True,
        },
        "source_ids": STRING_LIST_SCHEMA,
        "reports": {"type": "array", "items": {"type": "object"}},
        "payload": {"type": "object"},
        "background_knowledge": {
            "type": "object",
            "additionalProperties": False,
            "required": ["digest", "status", "shared_collections", "private_collections"],
            "properties": {
                "digest": ID_DEFINITIONS["digest"],
                "status": {"enum": ["current", "degraded"]},
                "shared_collections": STRING_LIST_SCHEMA,
                "private_collections": STRING_LIST_SCHEMA,
            },
        },
        "sidecar_digest": ID_DEFINITIONS["digest"],
        "evidence_snapshot_hash": ID_DEFINITIONS["snapshot_hash"],
        "input_digest": ID_DEFINITIONS["digest"],
        "created_at": {"type": "string", "format": "date-time"},
    },
}

IC_WORKFLOW_RUN_IDENTITY_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_WORKFLOW_RUN_IDENTITY_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "workflow_run_id",
        "deal_id",
        "research_identity",
        "evidence_snapshot_hash",
        "source_ids",
        "profile_contract_version",
        "started_at",
    ],
    "properties": {
        "schema_version": {"const": IC_WORKFLOW_RUN_IDENTITY_SCHEMA},
        "workflow_run_id": ID_DEFINITIONS["workflow_run_id"],
        "deal_id": ID_DEFINITIONS["deal_id"],
        "research_identity": {"type": "object", "minProperties": 1},
        "evidence_snapshot_hash": ID_DEFINITIONS["snapshot_hash"],
        "source_ids": STRING_LIST_SCHEMA,
        "profile_contract_version": {"type": "string", "minLength": 1, "maxLength": 100},
        "started_at": {"type": "string", "format": "date-time"},
    },
}

IC_WORKFLOW_RUN_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": IC_WORKFLOW_RUN_SCHEMA,
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "workflow_run_id",
        "deal_id",
        "status",
        "evidence_snapshot_hash",
        "source_ids",
        "active_sources",
        "created_at",
        "updated_at",
    ],
    "properties": {
        "schema_version": {"const": IC_WORKFLOW_RUN_SCHEMA},
        "workflow_run_id": ID_DEFINITIONS["workflow_run_id"],
        "deal_id": ID_DEFINITIONS["deal_id"],
        "status": {"enum": ["active", "superseded_by_snapshot", "completed", "cancelled"]},
        "evidence_snapshot_hash": {"anyOf": [ID_DEFINITIONS["snapshot_hash"], {"type": "null"}]},
        "source_ids": STRING_LIST_SCHEMA,
        "active_sources": {"type": "array", "items": {"type": "object"}},
        "created_by": {"type": ["object", "null"]},
        "created_at": {"type": "string", "format": "date-time"},
        "updated_at": {"type": "string", "format": "date-time"},
        "completed_at": {"type": "string", "format": "date-time"},
        "completion": {"type": "object"},
    },
}

TASK_CONTRACT_SCHEMAS = {
    IC_AGENT_TASK_SCHEMA: IC_AGENT_TASK_JSON_SCHEMA,
    IC_AGENT_HANDOFF_V1_SCHEMA: IC_AGENT_HANDOFF_V1_JSON_SCHEMA,
    IC_AGENT_HANDOFF_SCHEMA: IC_AGENT_HANDOFF_JSON_SCHEMA,
    IC_WORKFLOW_RUN_IDENTITY_SCHEMA: IC_WORKFLOW_RUN_IDENTITY_JSON_SCHEMA,
    IC_WORKFLOW_RUN_SCHEMA: IC_WORKFLOW_RUN_JSON_SCHEMA,
}


def canonical_input_digest(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_agent_task(
    payload: Mapping[str, Any],
    *,
    expected_deal_id: str | None = None,
    expected_agent_id: str | None = None,
    expected_snapshot_hash: str | None = None,
) -> dict[str, Any]:
    task = validate_schema(payload, IC_AGENT_TASK_JSON_SCHEMA, contract=IC_AGENT_TASK_SCHEMA)
    require_identity(
        task,
        contract=IC_AGENT_TASK_SCHEMA,
        expected_deal_id=expected_deal_id,
        expected_agent_id=expected_agent_id,
        expected_snapshot_hash=expected_snapshot_hash,
    )
    phase = str(task["phase"])
    agent_id = str(task["agent_id"])
    profile_contract = ic_profile_contract.get_ic_profile_contract(agent_id)
    errors: list[str] = []
    matrix_phases = set(profile_contract.get("phase_capabilities") or {})
    if phase not in matrix_phases:
        errors.append(f"agent_phase_not_allowed:{agent_id}:{phase}")
    allowed_outputs = set(ROLE_OUTPUT_SCHEMAS.get(agent_id, ())) | set(
        RUNTIME_OUTPUT_SCHEMAS_BY_PHASE.get(phase, ())
    )
    if task["output_schema"] not in allowed_outputs:
        errors.append(f"agent_output_schema_not_allowed:{agent_id}:{task['output_schema']}")
    retrieval = profile_contract.get("retrieval") or {}
    gate = task["startup_retrieval_gate"]
    background_refs = [*task["background_knowledge_refs"], *task["methodology_refs"]]
    ref_ids = [item["ref_id"] for item in background_refs]
    if len(ref_ids) != len(set(ref_ids)):
        errors.append("duplicate_background_knowledge_ref_id")
    if any(item["ref_id"].startswith("EVID-") for item in background_refs):
        errors.append("background_knowledge_masquerades_as_project_evidence")
    if any(item["collection"] != retrieval.get("private_collection") for item in background_refs):
        errors.append("background_knowledge_collection_not_owned_by_agent")
    if any(item["usage"] != "methodology" for item in task["methodology_refs"]):
        errors.append("methodology_ref_usage_mismatch")
    if retrieval.get("required") and not (
        gate["allowed_to_speak"]
        and gate["project_evidence_ready"]
        and gate["private_background_ready"]
        and not gate["blocking_reasons"]
        and background_refs
    ):
        errors.append("startup_retrieval_gate_not_ready")
    if gate["private_collection"] != retrieval.get("private_collection"):
        errors.append("startup_retrieval_private_collection_mismatch")
    if gate["shared_collection"] != retrieval.get("shared_collection"):
        errors.append("startup_retrieval_shared_collection_mismatch")
    research_identity = task["research_identity"]
    if retrieval.get("private_collection") not in research_identity.get("private_collections", []):
        errors.append("research_identity_private_collection_mismatch")
    if retrieval.get("shared_collection") not in research_identity.get("shared_collections", []):
        errors.append("research_identity_shared_collection_mismatch")
    combine_validation_errors(IC_AGENT_TASK_SCHEMA, errors)
    return task


def validate_agent_handoff(
    payload: Mapping[str, Any],
    *,
    expected_deal_id: str | None = None,
    expected_snapshot_hash: str | None = None,
) -> dict[str, Any]:
    schema_version = str(payload.get("schema_version") or "")
    if schema_version == IC_AGENT_HANDOFF_V1_SCHEMA:
        schema = IC_AGENT_HANDOFF_V1_JSON_SCHEMA
        contract = IC_AGENT_HANDOFF_V1_SCHEMA
    else:
        schema = IC_AGENT_HANDOFF_JSON_SCHEMA
        contract = IC_AGENT_HANDOFF_SCHEMA
    handoff = validate_schema(payload, schema, contract=contract)
    require_identity(
        handoff,
        contract=contract,
        expected_deal_id=expected_deal_id,
        expected_snapshot_hash=expected_snapshot_hash,
    )
    errors = []
    if handoff["from_agent_id"] == handoff["to_agent_id"]:
        errors.append("handoff_sender_equals_recipient")
    recipient_contract = ic_profile_contract.get_ic_profile_contract(handoff["to_agent_id"])
    if handoff["phase"] not in set(recipient_contract.get("phase_capabilities") or {}):
        errors.append("handoff_recipient_phase_not_allowed")
    digest_fields = (
        "workflow_run_id",
        "deal_id",
        "phase",
        "from_agent_id",
        "to_agent_id",
        "source_report_ids",
        "claim_ids",
        "dispute_ids",
        "evidence_ids",
        "evidence_snapshot_hash",
    ) if contract == IC_AGENT_HANDOFF_V1_SCHEMA else (
        "workflow_run_id",
        "deal_id",
        "phase",
        "from_agent_id",
        "to_agent_id",
        "source_report_ids",
        "claim_ids",
        "dispute_ids",
        "project_evidence_ids",
        "source_ids",
        "reports",
        "payload",
        "background_knowledge",
        "sidecar_digest",
        "evidence_snapshot_hash",
    )
    digest_body = {key: handoff[key] for key in digest_fields}
    if canonical_input_digest(digest_body) != handoff["input_digest"]:
        errors.append("handoff_input_digest_mismatch")
    if handoff["handoff_id"] != f"ICHANDOFF-{handoff['input_digest'][:24].upper()}":
        errors.append("handoff_id_digest_mismatch")
    combine_validation_errors(contract, errors)
    return handoff


def validate_workflow_run_identity(payload: Mapping[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") == IC_WORKFLOW_RUN_SCHEMA:
        return validate_schema(payload, IC_WORKFLOW_RUN_JSON_SCHEMA, contract=IC_WORKFLOW_RUN_SCHEMA)
    return validate_schema(
        payload,
        IC_WORKFLOW_RUN_IDENTITY_JSON_SCHEMA,
        contract=IC_WORKFLOW_RUN_IDENTITY_SCHEMA,
    )


def get_task_contract_schema(schema_version: str) -> dict[str, Any]:
    try:
        return TASK_CONTRACT_SCHEMAS[schema_version]
    except KeyError as exc:
        raise ValueError(f"Unknown IC task contract schema: {schema_version}") from exc


__all__ = [
    "ICContractValidationError",
    "IC_AGENT_HANDOFF_SCHEMA",
    "IC_AGENT_HANDOFF_V1_SCHEMA",
    "IC_AGENT_TASK_SCHEMA",
    "IC_WORKFLOW_RUN_IDENTITY_SCHEMA",
    "IC_WORKFLOW_RUN_SCHEMA",
    "ROLE_OUTPUT_SCHEMAS",
    "ROLE_PHASE_CAPABILITIES",
    "canonical_input_digest",
    "get_task_contract_schema",
    "validate_agent_handoff",
    "validate_agent_task",
    "validate_workflow_run_identity",
]
