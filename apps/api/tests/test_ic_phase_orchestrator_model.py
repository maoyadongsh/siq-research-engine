from __future__ import annotations

import asyncio
import hashlib
import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from services import deal_decision, deal_store, ic_phase_orchestrator, ic_report_contracts

DEAL_ID = "DEAL-IC-MODEL-001"
AGENT_ID = "siq_ic_strategist"
EVIDENCE_ID = "EVID-DEAL-IC-MODEL-001-000001"
SNAPSHOT = "a" * 64
KBREF_ID = "KBREF-STRATEGY-0001"
R2_AGENTS = tuple(ic_phase_orchestrator.R2_AGENT_IDS)


def _write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _package(tmp_path):
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="Model Contract Co",
        industry="Robotics",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / DEAL_ID
    _write_json(
        package_dir / "evidence" / "evidence_snapshot.json",
        {"snapshot_hash": SNAPSHOT, "source_ids": ["PM:SOURCE-001"], "active_sources": []},
    )
    (package_dir / "evidence" / "evidence_items.ndjson").write_text(
        json.dumps({"evidence_id": EVIDENCE_ID, "deal_id": DEAL_ID, "dimension": "business"}) + "\n",
        encoding="utf-8",
    )
    return package_dir


def test_completed_workflow_run_is_not_reused_for_new_tasks(tmp_path):
    package_dir = _package(tmp_path)
    first = ic_phase_orchestrator.ensure_workflow_run(package_dir)
    store_path = package_dir / ic_phase_orchestrator.WORKFLOW_RUNS_PATH
    store = deal_store.read_json(store_path, {})
    store["runs"][0]["status"] = "completed"
    store["runs"][0]["completed_at"] = "2026-07-14T00:00:00Z"
    deal_store.write_json(store_path, store)

    second = ic_phase_orchestrator.ensure_workflow_run(package_dir)

    assert second["workflow_run_id"] != first["workflow_run_id"]
    persisted = deal_store.read_json(store_path, {})
    assert [item["status"] for item in persisted["runs"]] == ["completed", "active"]
    assert persisted["active_workflow_run_id"] == second["workflow_run_id"]


def _receipt() -> dict:
    return {
        "schema_version": "siq_ic_startup_receipt_v2",
        "receipt_id": "startup-siq_ic_strategist-R1-001",
        "deal_id": DEAL_ID,
        "agent_id": AGENT_ID,
        "round_name": "R1",
        "phase": "R1A",
        "evidence_snapshot_hash": SNAPSHOT,
        "source_ids": ["PM:SOURCE-001"],
        "gate": {"allowed_to_speak": True, "blocking_reasons": []},
        "retrieval_status": "completed",
        "milvus_used": True,
        "private_hits": 1,
        "shared_collections": ["siq_deal_shared"],
        "private_collections": [AGENT_ID],
        "shared_collection": "siq_deal_shared",
        "private_collection": AGENT_ID,
        "retrieval_collections": ["siq_deal_shared", AGENT_ID],
        "project_evidence_hits": [{"evidence_id": EVIDENCE_ID, "deal_id": DEAL_ID}],
        "background_knowledge_hits": [
            {"id": "KB-STRATEGY-001", "collection": AGENT_ID, "title": "strategy diligence method"}
        ],
        "background_knowledge_refs": [
            {
                "ref_id": KBREF_ID,
                "collection": AGENT_ID,
                "locator": "KB-STRATEGY-001",
                "title": "strategy diligence method",
                "usage": "background",
            }
        ],
    }


def _phase_receipt(agent_id: str, round_name: str) -> dict:
    ref_id = f"KBREF-{agent_id.removeprefix('siq_ic_').replace('_', '-').upper()}-0001"
    return {
        **_receipt(),
        "receipt_id": f"startup-{agent_id}-{round_name}-001",
        "agent_id": agent_id,
        "round_name": round_name,
        "phase": round_name,
        "private_collections": [agent_id],
        "private_collection": agent_id,
        "retrieval_collections": ["siq_deal_shared", agent_id],
        "background_knowledge_hits": [
            {"id": f"KB-{agent_id}", "collection": agent_id, "title": "role method"}
        ],
        "background_knowledge_refs": [
            {
                "ref_id": ref_id,
                "collection": agent_id,
                "locator": f"KB-{agent_id}",
                "title": "role method",
                "usage": "background",
            }
        ],
    }


def _role_fields(agent_id: str) -> dict:
    if agent_id == "siq_ic_strategist":
        names = ("policy_assessment", "cycle_position", "capital_flow_signals", "strategic_fit", "scenario_matrix", "exit_window")
    elif agent_id == "siq_ic_sector_expert":
        names = ("market_sizing", "competitor_matrix", "technology_routes", "value_chain", "market_share_evidence", "industry_lifecycle")
    elif agent_id == "siq_ic_finance_auditor":
        names = (
            "historical_financials", "financial_reconciliations", "quality_of_earnings",
            "cash_flow_assessment", "forecast_scenarios", "valuation_scenarios", "sensitivity_analysis",
        )
    elif agent_id == "siq_ic_legal_scanner":
        names = (
            "legal_issues", "legal_basis", "severity", "remediation", "closing_conditions",
            "term_sheet_protections", "unresolved_legal_questions",
        )
    else:
        names = (
            "risk_register", "counter_theses", "stress_scenarios", "risk_transmission",
            "leading_indicators", "warning_thresholds", "stop_loss_thresholds", "veto_flags",
        )
    fields = {name: [{"result": "reviewed"}] for name in names}
    if agent_id == "siq_ic_finance_auditor":
        fields["calculation_trace_ids"] = ["CALC-R2-001"]
    if agent_id in {"siq_ic_legal_scanner", "siq_ic_risk_controller"}:
        fields["unresolved_legal_questions" if agent_id == "siq_ic_legal_scanner" else "veto_flags"] = []
    return fields


def _r2_model_output(agent_id: str) -> dict:
    receipt = _phase_receipt(agent_id, "R2")
    ref_id = receipt["background_knowledge_refs"][0]["ref_id"]
    claim_id = f"CLM-{agent_id.removeprefix('siq_ic_').replace('_', '-').upper()}-001"
    claim = {
        "claim_id": claim_id,
        "topic": "role_revision",
        "conclusion": "The R1 conclusion remains supported after the ruling review.",
        "status": "verified",
        "evidence_ids": [EVIDENCE_ID],
        "counter_evidence_ids": [],
        "calculation_trace_ids": [],
        "background_knowledge_ref_ids": [ref_id],
        "methodology_ref_ids": [],
        "confidence": "medium",
        "decision_impact": "material",
        "period": "2026",
        "currency": None,
        "unit": None,
    }
    return {
        "schema_version": "siq_ic_r2_revision_v1",
        "r1_score": 75,
        "r2_score": 75,
        "score_change": 0,
        "changed_claims": [],
        "unchanged_claims": [claim_id],
        "accepted_rulings": [],
        "challenged_rulings": [],
        "new_evidence_ids": [],
        "closed_questions": [],
        "remaining_questions": [],
        "revision_rationale": "No score change because the current Evidence still supports the role conclusion.",
        "report": {
            "recommendation": "conditional_support",
            "score": 75,
            "confidence": "medium",
            "claims": [claim],
            "scorecard": [
                {
                    "dimension": "role_revision",
                    "score": 75,
                    "weight": 1,
                    "rationale": "The project Evidence remains current.",
                    "claim_ids": [claim_id],
                    "evidence_ids": [EVIDENCE_ID],
                    "confidence": "medium",
                }
            ],
            "red_flags": [],
            "open_questions": [],
            "required_followups": [],
            "executive_summary": "R2 confirms the evidence-backed R1 conclusion.",
            "methodology": ["ruling and evidence delta review"],
            "limitations": ["No new project evidence was supplied."],
            **_role_fields(agent_id),
        },
    }


def _full_expert_report(agent_id: str, phase: str, *, revision: int = 1) -> dict:
    round_name = "R2" if phase == "R2" else "R1"
    receipt = _phase_receipt(agent_id, round_name)
    ref_id = receipt["background_knowledge_refs"][0]["ref_id"]
    claim_id = f"CLM-{agent_id.removeprefix('siq_ic_').replace('_', '-').upper()}-{phase.replace('.', '')}-001"
    role_fields = _role_fields(agent_id) if agent_id != "siq_ic_chairman" else {
        "consensus": [{"result": "conditional support"}],
        "disputes": [],
        "rulings": [],
        "six_dimension_scorecard": [{"dimension": f"D{i}"} for i in range(6)],
        "weighted_agent_score": 75,
        "chairman_dimension_score": 75,
        "chairman_qualitative_decision": "Support with conditions.",
        "conditions": [],
        "monitoring_metrics": [{"metric": "milestone"}],
        "decision": "pass",
    }
    return {
        "schema_version": "siq_ic_expert_report_v2",
        "report_id": f"ICRPT-{agent_id.removeprefix('siq_ic_').replace('_', '-').upper()}-{phase.replace('.', '')}-0001",
        "workflow_run_id": "ICRUN-REPORT-FIXTURE-0001",
        "deal_id": DEAL_ID,
        "phase": phase,
        "agent_id": agent_id,
        "research_identity": {"source_ids": ["PM:SOURCE-001"], "private_collection": agent_id},
        "evidence_snapshot_hash": SNAPSHOT,
        "recommendation": "conditional_support",
        "score": 75,
        "confidence": "medium",
        "claims": [{
            "claim_id": claim_id,
            "topic": "role_assessment",
            "conclusion": "The role assessment is supported by the cited project Evidence.",
            "status": "verified",
            "evidence_ids": [EVIDENCE_ID],
            "counter_evidence_ids": [],
            "calculation_trace_ids": [],
            "background_knowledge_ref_ids": [ref_id],
            "methodology_ref_ids": [],
            "confidence": "medium",
            "decision_impact": "material",
            "period": "2026",
            "currency": None,
            "unit": None,
        }],
        "scorecard": [{
            "dimension": "role_assessment",
            "score": 75,
            "weight": 1,
            "rationale": "The cited Evidence supports the score.",
            "claim_ids": [claim_id],
            "evidence_ids": [EVIDENCE_ID],
            "confidence": "medium",
        }],
        "red_flags": [],
        "open_questions": [],
        "required_followups": [],
        "executive_summary": "The role assessment conditionally supports the project.",
        "methodology": ["role-specific project diligence"],
        "background_knowledge_refs": receipt["background_knowledge_refs"],
        "methodology_refs": [],
        "startup_receipt_id": receipt["receipt_id"],
        "startup_retrieval_gate": {
            "receipt_id": receipt["receipt_id"],
            "allowed_to_speak": True,
            "project_evidence_ready": True,
            "private_background_ready": True,
            "shared_collection": "siq_deal_shared",
            "private_collection": agent_id,
            "blocking_reasons": [],
        },
        "limitations": [],
        "generation_mode": "model",
        "revision": revision,
        "parent_report_id": None,
        "created_at": "2026-07-13T10:00:00+08:00",
        **role_fields,
    }


def _peer_claim(
    claim_id: str,
    *,
    topic: str,
    impact: str = "material",
    evidence_id: str = EVIDENCE_ID,
) -> dict:
    return {
        "claim_id": claim_id,
        "topic": topic,
        "conclusion": f"Evidence-backed conclusion for {topic}.",
        "status": "verified",
        "evidence_ids": [evidence_id],
        "counter_evidence_ids": [],
        "calculation_trace_ids": [],
        "background_knowledge_ref_ids": [],
        "methodology_ref_ids": [],
        "confidence": "medium",
        "decision_impact": impact,
        "period": "2026",
        "currency": None,
        "unit": None,
    }


def _peer_report(agent_id: str, claims: list[dict]) -> dict:
    label = agent_id.removeprefix("siq_ic_").replace("_", "-").upper()
    return {
        "report_id": f"ICRPT-{label}-R1-PEER",
        "agent_id": agent_id,
        "claims": claims,
    }


def _r4_model_output() -> dict:
    claim_id = "CLM-CHAIRMAN-R4-001"
    claim = {
        "claim_id": claim_id,
        "topic": "investment_case",
        "conclusion": "The current project Evidence supports a conditional investment decision.",
        "status": "verified",
        "evidence_ids": [EVIDENCE_ID],
        "counter_evidence_ids": [],
        "calculation_trace_ids": [],
        "background_knowledge_ref_ids": ["KBREF-CHAIRMAN-0001"],
        "methodology_ref_ids": [],
        "confidence": "medium",
        "decision_impact": "material",
        "period": "2026",
        "currency": None,
        "unit": None,
    }
    dimension_weights = {
        "market_attraction": 0.15,
        "team_execution": 0.30,
        "product_competitiveness": 0.20,
        "financial_reasonableness": 0.15,
        "risk_controllability": 0.10,
        "strategic_alignment": 0.10,
    }
    dimensions = [
        {
            "dimension": name,
            "score": 75,
            "weight": weight,
            "rationale": f"{name} is supported by current project Evidence.",
            "claim_ids": [claim_id],
            "evidence_ids": [EVIDENCE_ID],
            "confidence": "medium",
        }
        for name, weight in dimension_weights.items()
    ]
    return {
        "recommendation": "conditional_support",
        "decision": "pass",
        "chairman_dimension_score": 75,
        "chairman_qualitative_decision": "Proceed with explicit conditions.",
        "six_dimension_scorecard": dimensions,
        "claims": [claim],
        "executive_summary": "The committee can proceed subject to defined conditions.",
        "decision_rationale": "The six dimensions and current Evidence support conditional approval.",
        "score_delta_explanation": "R4 holds the score at 75 after resolving the R3 topic.",
        "verified_facts": [{"claim": "Current Evidence is indexed.", "evidence_ids": [EVIDENCE_ID]}],
        "assumptions": [],
        "core_disputes": [],
        "conditions": [{"condition": "Complete final legal verification."}],
        "monitoring_metrics": [{"metric": "legal verification", "threshold": "completed"}],
        "principal_risks": [{"risk": "execution timing", "evidence_ids": [EVIDENCE_ID]}],
        "valuation_and_exit": [{"conclusion": "Review at the next evidence refresh."}],
    }


def _model_output() -> dict:
    claim = {
        "claim_id": "CLM-STRATEGY-001",
        "topic": "policy_fit",
        "conclusion": "Project Evidence supports a conditional policy fit conclusion.",
        "status": "verified",
        "evidence_ids": [EVIDENCE_ID],
        "counter_evidence_ids": [],
        "calculation_trace_ids": [],
        "background_knowledge_ref_ids": [KBREF_ID],
        "methodology_ref_ids": [],
        "confidence": "medium",
        "decision_impact": "material",
        "period": "2026",
        "currency": None,
        "unit": None,
    }
    scorecard = {
        "dimension": "strategy",
        "score": 76,
        "weight": 1,
        "rationale": "The cited project Evidence supports the conditional view.",
        "claim_ids": [claim["claim_id"]],
        "evidence_ids": [EVIDENCE_ID],
        "confidence": "medium",
    }
    return {
        "recommendation": "conditional_support",
        "score": 76,
        "confidence": "medium",
        "claims": [claim],
        "scorecard": [scorecard],
        "red_flags": [],
        "open_questions": ["Validate exit timing."],
        "required_followups": ["Refresh policy evidence before IC."],
        "executive_summary": "The strategy case is conditionally supportable.",
        "methodology": ["policy and cycle assessment"],
        "limitations": ["Exit timing remains uncertain."],
        "policy_assessment": {"result": "conditional"},
        "cycle_position": {"result": "mid-cycle"},
        "capital_flow_signals": {"result": "mixed"},
        "strategic_fit": {"result": "positive"},
        "scenario_matrix": [{"scenario": "base", "outcome": "support"}],
        "exit_window": {"result": "2027-2028"},
    }


def test_extract_json_object_accepts_only_the_complete_json_object():
    payload = {"decision": "review", "score": 71}

    assert ic_phase_orchestrator._extract_json_object(
        "  \n" + json.dumps(payload) + "\n  "
    ) == payload


def test_extract_evidence_ids_ignores_descriptive_composite_strings():
    assert ic_phase_orchestrator._extract_evidence_ids(
        {
            "claims": [
                {"evidence_ids": ["EVID-PMIC-VALID-001"]},
                {"counter_evidence_ids": ["not-an-evidence-id"]},
            ],
            "source_basis": "EVID-PMIC-VALID-001/002/003/004",
            "note": "EVID-PMIC-VALID-002",
        }
    ) == ["EVID-PMIC-VALID-001", "EVID-PMIC-VALID-002"]


@pytest.mark.parametrize(
    "output",
    [
        '```json\n{"decision":"review"}\n```',
        'analysis before\n{"decision":"review"}',
        '{"decision":"review"}\nanalysis after',
        '[{"decision":"review"}]',
        '{"decision":"review"}{"score":71}',
        '"not an object"',
    ],
)
def test_extract_json_object_rejects_wrappers_arrays_and_multiple_objects(output):
    with pytest.raises(ValueError, match="response_must_be_single_json_object"):
        ic_phase_orchestrator._extract_json_object(output)


def test_phase_and_repair_prompts_include_authoritative_r0_json_schema():
    task = {
        "task_id": "ICTASK-PROMPT-R0",
        "agent_id": "siq_ic_master_coordinator",
        "output_schema": "siq_ic_r0_readiness_v1",
    }

    phase_prompt = ic_phase_orchestrator._phase_prompt(task, {"handoff_id": "ICHAND-R0"})
    repair_prompt = ic_phase_orchestrator._repair_prompt(
        task=task,
        handoff={"handoff_id": "ICHAND-R0"},
        invalid_output='{"readiness":"ready"}',
        error=ValueError("due_diligence_plan is required"),
    )

    for prompt in (phase_prompt, repair_prompt):
        assert "ic_report_contracts.get_report_contract_schema" in prompt
        assert '"type": "object"' in prompt
        assert '"required": [' in prompt
        assert '"readiness"' in prompt
        assert '"due_diligence_plan"' in prompt
        assert '"task_assignments"' in prompt
        assert '"material_completeness": {' in prompt
        assert '"type": "object"' in prompt
        assert "single final top-level closing brace" in prompt
        assert "never emit a second top-level object" in prompt


@pytest.mark.parametrize(
    "schema_version",
    [
        ic_report_contracts.IC_R1_5_CHAIRMAN_RULINGS_SCHEMA,
        ic_report_contracts.IC_R3_DEBATE_TURN_SCHEMA,
        ic_report_contracts.IC_R3_DEBATE_VERDICT_SCHEMA,
    ],
)
def test_model_only_phase_schemas_use_shared_report_contract_registry(schema_version):
    task = {
        "task_id": f"ICTASK-PROMPT-{schema_version}",
        "agent_id": "siq_ic_chairman",
        "output_schema": schema_version,
    }

    source, schema = ic_phase_orchestrator._prompt_output_contract(task)

    assert source == "ic_report_contracts.get_report_contract_schema"
    assert schema == ic_report_contracts.get_report_contract_schema(schema_version)
    assert schema["$id"] == schema_version
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False


def test_shared_model_only_phase_schemas_preserve_prompt_contract_constraints():
    rulings = ic_report_contracts.get_report_contract_schema(
        ic_report_contracts.IC_R1_5_CHAIRMAN_RULINGS_SCHEMA
    )
    ruling_item = rulings["properties"]["rulings"]["items"]
    assert ruling_item["required"] == [
        "dispute_id",
        "ruling",
        "rationale",
        "required_followups",
        "evidence_ids",
        "counter_evidence_ids",
        "accepted_claim_ids",
        "rejected_claim_ids",
        "decision_impact",
    ]
    assert ruling_item["properties"]["ruling"]["enum"] == [
        "accept_a",
        "accept_b",
        "synthesize",
        "needs_more_evidence",
        "unresolved",
        "resolved_with_conditions",
        "resolved_no_followup",
    ]

    turn = ic_report_contracts.get_report_contract_schema(
        ic_report_contracts.IC_R3_DEBATE_TURN_SCHEMA
    )
    assert turn["properties"]["evidence_ids"]["minItems"] == 1

    verdict = ic_report_contracts.get_report_contract_schema(
        ic_report_contracts.IC_R3_DEBATE_VERDICT_SCHEMA
    )
    assert verdict["properties"]["outcome"]["enum"] == [
        "red_prevails",
        "blue_prevails",
        "synthesize",
        "needs_more_evidence",
        "unresolved",
    ]
    assert verdict["anyOf"] == [
        {"properties": {"accepted_argument_ids": {"minItems": 1}}},
        {"properties": {"rejected_argument_ids": {"minItems": 1}}},
    ]


def test_expert_prompts_include_current_role_required_fields_and_non_empty_rule():
    task = {
        "task_id": "ICTASK-PROMPT-STRATEGIST",
        "agent_id": AGENT_ID,
        "output_schema": "siq_ic_expert_report_v2",
    }

    phase_prompt = ic_phase_orchestrator._phase_prompt(task, None)
    repair_prompt = ic_phase_orchestrator._repair_prompt(
        task=task,
        handoff=None,
        invalid_output='{"score":76}',
        error=ValueError("role_field_missing_or_empty:policy_assessment"),
    )

    for prompt in (phase_prompt, repair_prompt):
        assert "ic_report_contracts.ROLE_REQUIRED_FIELDS" in prompt
        assert '"policy_assessment"' in prompt
        assert '"cycle_position"' in prompt
        assert '"capital_flow_signals"' in prompt
        assert '"strategic_fit"' in prompt
        assert '"scenario_matrix"' in prompt
        assert '"exit_window"' in prompt
        assert "must be present and non-null" in prompt
        assert "must be non-empty" in prompt
        assert "HARD LIMIT" in prompt
        assert "at most 6 claims" in prompt
        assert "Every scorecard item must cite" in prompt
        assert "authoritative server-managed field override" in prompt
        assert "MUST be omitted from the model response" in prompt
        assert "authoritative custom validator invariants" in prompt
        assert "Every critical or material claim MUST contain" in prompt
        assert "A derived claim MUST contain project Evidence IDs" in prompt
        assert "Do not create claims merely to discuss irrelevant" in prompt
        assert "Claims may cite task-envelope KBREF values" in prompt


def test_repair_prompt_projects_only_minimal_trusted_context_and_bounds_invalid_output():
    task = {
        "task_id": "ICTASK-PROMPT-STRATEGIST",
        "workflow_run_id": "ICRUN-PROMPT-STRATEGIST",
        "deal_id": DEAL_ID,
        "phase": "R1A",
        "round_name": "R1",
        "agent_id": AGENT_ID,
        "evidence_snapshot_hash": SNAPSHOT,
        "input_digest": "b" * 64,
        "output_schema": ic_report_contracts.IC_EXPERT_REPORT_SCHEMA,
        "prompt_contract_version": "siq_ic_phase_prompt_v5",
        "role_objectives": ["TASK-SENSITIVE-REDUNDANCY"],
        "background_knowledge_refs": [{"ref_id": KBREF_ID, "secret": "TASK-SECRET"}],
        "methodology_refs": [{"ref_id": "KBREF-METHOD-0001", "secret": "METHOD-SECRET"}],
    }
    handoff = {
        "contract": {
            "handoff_id": "ICHANDOFF-PROMPT-STRATEGIST",
            "workflow_run_id": "ICRUN-PROMPT-STRATEGIST",
            "deal_id": DEAL_ID,
            "phase": "R1A",
            "from_agent_id": "siq_ic_master_coordinator",
            "to_agent_id": AGENT_ID,
            "evidence_snapshot_hash": SNAPSHOT,
            "input_digest": "c" * 64,
            "payload": {"sensitive": "HANDOFF-SENSITIVE-REDUNDANCY"},
        },
        "content": {
            "project_evidence_hits": [{"evidence_id": EVIDENCE_ID}],
            "sensitive": "HANDOFF-CONTENT-SENSITIVE-REDUNDANCY",
        },
    }
    invalid_output = json.dumps(
        {
            "claims": [
                {
                    "claim_id": "CLM-STRAT-EXIT-005",
                    "conclusion": "x" * 25_000 + "INVALID-OUTPUT-TAIL-SENTINEL",
                }
            ]
        }
    )

    prompt = ic_phase_orchestrator._repair_prompt(
        task=task,
        handoff=handoff,
        invalid_output=invalid_output,
        error=ValueError(
            "decision_relevant_claim_requires_evidence:CLM-STRAT-EXIT-005"
        ),
    )

    assert len(prompt) < 38_000
    assert "TASK-SENSITIVE-REDUNDANCY" not in prompt
    assert "TASK-SECRET" not in prompt
    assert "METHOD-SECRET" not in prompt
    assert "HANDOFF-SENSITIVE-REDUNDANCY" not in prompt
    assert "HANDOFF-CONTENT-SENSITIVE-REDUNDANCY" not in prompt
    assert "task envelope:\n" not in prompt
    assert "validated handoff:\n" not in prompt
    assert '"task_id": "ICTASK-PROMPT-STRATEGIST"' in prompt
    assert '"handoff_id": "ICHANDOFF-PROMPT-STRATEGIST"' in prompt
    assert EVIDENCE_ID in prompt
    assert KBREF_ID in prompt
    assert "KBREF-METHOD-0001" in prompt
    assert '"included_char_count":16000' in prompt
    assert '"truncated":true' in prompt
    assert "INVALID-OUTPUT-TAIL-SENTINEL" not in prompt
    assert "禁止调用任何工具" in prompt
    assert "最终输出仍会经过完整正式 validator" in prompt


def test_expert_model_authoring_schema_omits_server_managed_fields():
    task = {
        "task_id": "ICTASK-PROMPT-STRATEGIST",
        "agent_id": AGENT_ID,
        "output_schema": "siq_ic_expert_report_v2",
    }

    source, schema = ic_phase_orchestrator._prompt_model_output_contract(task)

    assert source.endswith("ic_phase_orchestrator.server_authoring_projection")
    assert schema["x-persisted-final-contract"] == "siq_ic_expert_report_v2"
    assert schema["x-projection"] == "server_managed_fields_omitted"
    for field in (
        "schema_version",
        "report_id",
        "research_identity",
        "background_knowledge_refs",
        "methodology_refs",
        "created_at",
    ):
        assert field not in schema["required"]
        assert field not in schema["properties"]
    assert "claims" in schema["required"]
    assert "recommendation" in schema["properties"]


def test_r2_prompt_omits_nested_server_managed_report_fields():
    task = {
        "task_id": "ICTASK-PROMPT-R2-STRATEGIST",
        "agent_id": AGENT_ID,
        "output_schema": "siq_ic_r2_revision_v1",
    }

    prompt = ic_phase_orchestrator._phase_prompt(task, None)
    _, schema = ic_phase_orchestrator._prompt_model_output_contract(task)
    report_schema = schema["properties"]["report"]

    assert "schema_version" not in schema["properties"]
    assert "report.background_knowledge_refs" in prompt
    assert "report.methodology_refs" in prompt
    assert "report.research_identity" in prompt
    assert "report.created_at" in prompt
    assert "r1_score MUST exactly match" in prompt
    assert "MUST be omitted from the model response" in prompt
    assert "background_knowledge_refs" not in report_schema["properties"]
    assert "created_at" not in report_schema["required"]


def test_r4_prompt_projects_server_fields_and_exposes_full_authoring_contract():
    task = {
        "task_id": "ICTASK-PROMPT-R4-CHAIRMAN",
        "agent_id": "siq_ic_chairman",
        "output_schema": "siq_ic_r4_decision_v2",
    }

    prompt = ic_phase_orchestrator._phase_prompt(task, None)
    source, schema = ic_phase_orchestrator._prompt_model_output_contract(task)

    assert source.endswith("ic_phase_orchestrator.server_authoring_projection")
    assert schema["x-persisted-final-contract"] == "siq_ic_r4_decision_v2"
    for field in (
        "schema_version",
        "report_id",
        "workflow_run_id",
        "research_identity",
        "weighted_agent_score",
        "threshold_result",
        "created_at",
    ):
        assert field not in schema["required"]
        assert field not in schema["properties"]
    for field in (
        "claims",
        "six_dimension_scorecard",
        "executive_summary",
        "decision_rationale",
        "verified_facts",
        "assumptions",
        "core_disputes",
        "principal_risks",
        "valuation_and_exit",
    ):
        assert field in schema["required"]
        assert field in schema["properties"]
    assert "below 12000 characters" in prompt
    assert "match a claim_id in claims byte-for-byte" in prompt
    assert "weights MUST sum to exactly 1 or exactly 100" in prompt
    assert "chairman_scoring_policy.dimensions" in prompt
    assert "MUST be omitted from the model response" in prompt


def test_r4_model_fixture_matches_advertised_authoring_schema():
    task = {
        "task_id": "ICTASK-PROMPT-R4-CHAIRMAN",
        "agent_id": "siq_ic_chairman",
        "output_schema": "siq_ic_r4_decision_v2",
    }
    _, schema = ic_phase_orchestrator._prompt_model_output_contract(task)

    Draft202012Validator(schema).validate(_r4_model_output())


@pytest.mark.parametrize("field", ic_phase_orchestrator._R4_DECISION_SERVER_MANAGED_FIELDS)
def test_r4_authoring_schema_rejects_server_managed_fields(field):
    task = {
        "task_id": "ICTASK-PROMPT-R4-CHAIRMAN",
        "agent_id": "siq_ic_chairman",
        "output_schema": "siq_ic_r4_decision_v2",
    }
    _, schema = ic_phase_orchestrator._prompt_model_output_contract(task)
    payload = _r4_model_output()
    payload[field] = "model-authored-value"

    assert list(Draft202012Validator(schema).iter_errors(payload))


def test_r4_validator_keeps_legacy_aliases_for_persisted_raw_replay(tmp_path):
    package_dir = _package(tmp_path)
    workflow_run = ic_phase_orchestrator.ensure_workflow_run(package_dir)
    receipt = _phase_receipt("siq_ic_chairman", "R4")
    task = ic_phase_orchestrator.build_task_envelope(
        package_dir,
        workflow_run=workflow_run,
        phase="R4",
        round_name="R4",
        agent_id="siq_ic_chairman",
        receipt=receipt,
        handoff=None,
        role_objectives=["Issue the final decision"],
        required_questions=["Why?"],
        output_schema=ic_report_contracts.IC_R4_DECISION_SCHEMA,
    )
    legacy = _r4_model_output()
    dimensions = legacy.pop("six_dimension_scorecard")
    legacy["dimension_scores"] = {
        item["dimension"]: {key: value for key, value in item.items() if key != "dimension"}
        for item in dimensions
    }
    legacy["decision"] = "conditional_pass"

    validated = ic_phase_orchestrator._r4_validator(
        package_dir,
        policy=ic_phase_orchestrator.ic_policy.read_ic_workflow_policy(),
        weighted_agent_score=75,
        task=task,
        veto_flags=[],
        unresolved_high_disputes=[],
    )(legacy)

    assert validated["decision"] == "pass"
    assert validated["recommendation"] == "conditional_support"
    assert len(validated["six_dimension_scorecard"]) == 6


def test_prompts_fail_closed_for_unknown_output_schema():
    task = {
        "task_id": "ICTASK-PROMPT-UNKNOWN",
        "agent_id": AGENT_ID,
        "output_schema": "siq_ic_not_registered_v99",
    }

    with pytest.raises(ValueError, match="unknown output_schema 'siq_ic_not_registered_v99'"):
        ic_phase_orchestrator._phase_prompt(task, None)
    with pytest.raises(ValueError, match="unknown output_schema 'siq_ic_not_registered_v99'"):
        ic_phase_orchestrator._repair_prompt(
            task=task,
            handoff=None,
            invalid_output="{}",
            error=ValueError("invalid"),
        )


def test_contract_repair_non_escalation_accepts_projection_and_safe_missing_downgrade():
    original = _model_output()
    original["claims"][0].update(
        {
            "status": "assumed",
            "evidence_ids": [],
            "assumption": "Exit timing remains assumed.",
            "verification_method": "Obtain current project Evidence.",
            "model_only_note": "remove during schema projection",
        }
    )
    repaired = deepcopy(original)
    repaired["claims"][0]["status"] = "missing"
    repaired["claims"][0].pop("model_only_note")

    ic_phase_orchestrator._verify_contract_repair_non_escalation(original, repaired)


def test_contract_repair_non_escalation_rejects_semantic_and_reference_changes():
    original = _model_output()

    changed = deepcopy(original)
    changed["recommendation"] = "support"
    with pytest.raises(ValueError, match="top_level_recommendation_changed"):
        ic_phase_orchestrator._verify_contract_repair_non_escalation(original, changed)

    changed = deepcopy(original)
    changed["claims"].pop()
    with pytest.raises(ValueError, match="claim_identity_changed"):
        ic_phase_orchestrator._verify_contract_repair_non_escalation(original, changed)

    changed = deepcopy(original)
    changed["claims"][0]["conclusion"] = "A stronger unsupported conclusion."
    with pytest.raises(ValueError, match="conclusion_changed"):
        ic_phase_orchestrator._verify_contract_repair_non_escalation(original, changed)

    changed = deepcopy(original)
    changed["claims"][0]["evidence_ids"].append("EVID-DEAL-IC-MODEL-001-999999")
    with pytest.raises(ValueError, match="references_added:EVID-DEAL-IC-MODEL-001-999999"):
        ic_phase_orchestrator._verify_contract_repair_non_escalation(original, changed)

    missing = deepcopy(original)
    missing["claims"][0]["status"] = "missing"
    upgraded = deepcopy(missing)
    upgraded["claims"][0]["status"] = "verified"
    with pytest.raises(ValueError, match="status_escalated"):
        ic_phase_orchestrator._verify_contract_repair_non_escalation(missing, upgraded)

    changed = deepcopy(original)
    changed["scorecard"][0]["claim_ids"] = ["CLM-STRATEGY-999"]
    with pytest.raises(ValueError, match="scorecard_claim_references_changed"):
        ic_phase_orchestrator._verify_contract_repair_non_escalation(original, changed)


def test_factcheck_repair_non_escalation_rejects_fail_to_pass_and_finding_deletion():
    original = {
        "schema_version": "siq_ic_report_factcheck_v1",
        "status": "fail",
        "claim_checks": [],
        "numeric_checks": [],
        "citation_checks": [],
        "contradictions": [],
        "unsupported_claims": [],
        "required_repairs": [
            {"id": "REPAIR-001", "message": "Remove the unsupported claim."}
        ],
    }
    repaired = {**deepcopy(original), "status": "pass", "required_repairs": []}

    with pytest.raises(ValueError) as exc:
        ic_phase_orchestrator._verify_contract_repair_non_escalation(
            original,
            repaired,
            factcheck=True,
        )
    assert "top_level_status_changed" in str(exc.value)
    assert "required_repairs:count_changed" in str(exc.value)


def test_r1_model_task_uses_v2_contract_private_kb_and_server_renderer(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    calls: list[tuple[str, str]] = []
    prompts: list[str] = []

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        assert history == []
        assert "validated handoff" in prompt
        assert "background knowledge" in prompt.lower() or "背景知识" in prompt
        calls.append((profile, session_id or ""))
        prompts.append(prompt)
        return "run-r1-model-001"

    async def fake_collect(run_id, *, profile, timeout=None):
        assert run_id == "run-r1-model-001"
        assert profile == AGENT_ID
        output = json.dumps(_model_output(), ensure_ascii=False)
        runtime = ic_phase_orchestrator.hermes_client.RunRuntimeMetadata(
            requested_model=AGENT_ID,
            configured_provider="minimax-cn",
            configured_model="MiniMax-M3",
            effective_provider="custom:stepfun-step-3.7-flash",
            effective_model="step-3.7-flash",
            fallback_activated=True,
        )
        ic_phase_orchestrator.hermes_client._remember_run_terminal(
            ic_phase_orchestrator.hermes_client.RunTerminalResult(
                run_id=run_id,
                status="succeeded",
                received_text=output,
                runtime=runtime,
            )
        )
        return output

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    result = asyncio.run(
        ic_phase_orchestrator.run_r1_model_task(
            package_dir,
            agent_id=AGENT_ID,
            receipt=_receipt(),
        )
    )

    assert calls and result["report"]["schema_version"] == "siq_ic_expert_report_v2"
    assert result["report"]["phase"] == "R1A"
    assert result["report"]["background_knowledge_refs"][0]["ref_id"] == KBREF_ID
    assert result["report"]["generation_mode"] == "model"
    assert result["execution"]["task"]["prompt_contract_version"] == "siq_ic_phase_prompt_v5"
    model_audit = result["execution"]["task"]["model_execution_audit"]
    assert model_audit["runtime_metadata_status"] == "verified"
    assert model_audit["attempt_count"] == 1
    assert model_audit["final_hermes_run_id"] == "run-r1-model-001"
    assert model_audit["final_prompt_sha256"] == hashlib.sha256(prompts[0].encode()).hexdigest()
    assert model_audit["final_runtime"]["effective"] == {
        "provider": "custom:stepfun-step-3.7-flash",
        "model": "step-3.7-flash",
    }
    assert model_audit["final_runtime"]["fallback"] == {"activated": True}
    validated_task = ic_phase_orchestrator.ic_task_contracts.validate_agent_task(
        result["execution"]["task"],
        expected_deal_id=DEAL_ID,
        expected_agent_id=AGENT_ID,
        expected_snapshot_hash=SNAPSHOT,
    )
    assert validated_task["model_execution_audit"] == model_audit
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})
    completion = next(
        event for event in audit["events"] if event["event_type"] == "ic_phase_hermes_task_completed"
    )
    assert completion["model_execution_audit"] == model_audit
    assert "## 角色专属分析" in result["markdown"]
    assert "```json" not in result["markdown"]


def test_r0_coordinator_uses_private_kb_and_writes_readiness_plan(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    coordinator = "siq_ic_master_coordinator"
    receipt = _phase_receipt(coordinator, "R0")
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v2",
            "deal_id": DEAL_ID,
            "agents": {coordinator: receipt},
            "by_agent_phase": {coordinator: {"R0": receipt}},
        },
    )

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        assert profile == coordinator
        assert '"output_schema": "siq_ic_r0_readiness_v1"' in prompt
        return "run-r0-coordinator"

    async def fake_collect(run_id, *, profile, timeout=None):
        return json.dumps(
            {
                "readiness": "ready",
                "material_completeness": {"prospectus": "available", "evidence": "indexed"},
                "evidence_gaps": [],
                "due_diligence_plan": [{"workstream": "industry", "priority": "high"}],
                "task_assignments": [{"agent_id": "siq_ic_sector_expert", "scope": "market"}],
                "blocking_reasons": [],
            }
        )

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    result = asyncio.run(ic_phase_orchestrator.run_r0_model(package_dir))

    assert result["workflow_advanced"] is True
    assert result["readiness"]["schema_version"] == "siq_ic_r0_readiness_v1"
    assert result["readiness"]["agent_id"] == coordinator
    assert (package_dir / "phases" / "r0_readiness.json").is_file()


def test_r1_model_task_repairs_invalid_contract_once(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    run_ids = iter(["run-invalid", "run-repair"])
    prompts: list[str] = []
    invalid_output = deepcopy(_model_output())
    invalid_output["claims"][0].update(
        {
            "claim_id": "CLM-STRAT-EXIT-005",
            "status": "assumed",
            "evidence_ids": [],
            "assumption": "Exit timing is assumed.",
            "verification_method": "Obtain current exit-market Evidence.",
        }
    )
    invalid_output["scorecard"][0]["claim_ids"] = ["CLM-STRAT-EXIT-005"]

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        prompts.append(prompt)
        return next(run_ids)

    repaired_output = deepcopy(invalid_output)
    repaired_output["claims"][0]["status"] = "missing"

    async def fake_collect(run_id, *, profile, timeout=None):
        if run_id == "run-invalid":
            return json.dumps(invalid_output, ensure_ascii=False)
        return json.dumps(repaired_output, ensure_ascii=False)

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    result = asyncio.run(
        ic_phase_orchestrator.run_r1_model_task(
            package_dir,
            agent_id=AGENT_ID,
            receipt=_receipt(),
        )
    )

    assert result["execution"]["repair_attempted"] is True
    assert result["report"]["claims"][0]["status"] == "missing"
    assert result["report"]["hermes_run_ids"] == ["run-invalid", "run-repair"]
    assert len(prompts) == 2
    assert (
        "decision_relevant_claim_requires_evidence:CLM-STRAT-EXIT-005"
        in prompts[1]
    )
    assert EVIDENCE_ID in prompts[1]
    assert KBREF_ID in prompts[1]
    assert "validated handoff:\n" not in prompts[1]
    task = result["execution"]["task"]
    assert set(task["output_artifact_hashes"]) == set(task["output_artifact_paths"])
    for relative_path, expected_hash in task["output_artifact_hashes"].items():
        assert hashlib.sha256((package_dir / relative_path).read_bytes()).hexdigest() == expected_hash
    assert task["output_artifact_hash"] == task["output_artifact_hashes"][task["output_artifact_path"]]
    assert task["contract_validation"] == {
        "passed": True,
        "output_schema": "siq_ic_expert_report_v2",
        "artifact_schema": "siq_ic_expert_report_v2",
        "validated_by": "ic_phase_orchestrator",
    }
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})
    assert any(event["event_type"] == "ic_phase_hermes_contract_repair_attempted" for event in audit["events"])
    completion = next(event for event in audit["events"] if event["event_type"] == "ic_phase_hermes_task_completed")
    assert completion["prompt_contract_version"] == "siq_ic_phase_prompt_v5"
    assert completion["handoff_digest"] == task["handoff_digest"]
    assert completion["output_artifact_hashes"] == task["output_artifact_hashes"]
    assert completion["contract_validation"]["passed"] is True


def test_r1_model_task_does_not_repair_unparseable_output(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    create_calls = 0

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        nonlocal create_calls
        create_calls += 1
        return "run-unparseable"

    async def fake_collect(run_id, *, profile, timeout=None):
        return 'preface\n{"recommendation":"reject"}\nsuffix'

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)

    with pytest.raises(ValueError, match="response_must_be_single_json_object"):
        asyncio.run(
            ic_phase_orchestrator.run_r1_model_task(
                package_dir,
                agent_id=AGENT_ID,
                receipt=_receipt(),
            )
        )
    assert create_calls == 1


def test_r1_model_task_revalidates_and_rejects_invalid_repair_output(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    run_ids = iter(["run-invalid", "run-still-invalid-repair"])
    invalid_output = deepcopy(_model_output())
    invalid_output["claims"][0].update(
        {
            "claim_id": "CLM-STRAT-EXIT-005",
            "status": "assumed",
            "evidence_ids": [],
            "assumption": "Exit timing is assumed.",
            "verification_method": "Obtain current exit-market Evidence.",
        }
    )
    invalid_output["scorecard"][0]["claim_ids"] = ["CLM-STRAT-EXIT-005"]

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        return next(run_ids)

    async def fake_collect(run_id, *, profile, timeout=None):
        return json.dumps(invalid_output, ensure_ascii=False)

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)

    with pytest.raises(
        ValueError,
        match="decision_relevant_claim_requires_evidence:CLM-STRAT-EXIT-005",
    ):
        asyncio.run(
            ic_phase_orchestrator.run_r1_model_task(
                package_dir,
                agent_id=AGENT_ID,
                receipt=_receipt(),
            )
        )

    task_store = deal_store.read_json(package_dir / "phases" / "ic_agent_tasks.json", {})
    persisted = next(item for item in task_store["tasks"] if item["agent_id"] == AGENT_ID)
    assert persisted["status"] == "failed"
    assert persisted["hermes_run_ids"] == ["run-invalid", "run-still-invalid-repair"]
    assert persisted["contract_validation"]["passed"] is False


def test_r1_model_task_wall_clock_timeout_stops_remote_run_and_persists_timeout(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    stop_calls: list[tuple[str, str]] = []

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        return "run-wall-clock-timeout"

    async def fake_collect(run_id, *, profile, timeout=None):
        await asyncio.Event().wait()

    async def fake_stop(run_id, *, profile):
        stop_calls.append((run_id, profile))
        return {"stopped": True}

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "stop_run", fake_stop)

    with pytest.raises(ic_phase_orchestrator.ICTaskWallClockTimeout, match="wall-clock timeout"):
        asyncio.run(
            ic_phase_orchestrator.run_r1_model_task(
                package_dir,
                agent_id=AGENT_ID,
                receipt=_receipt(),
                timeout=0.02,
            )
        )

    assert stop_calls == [("run-wall-clock-timeout", AGENT_ID)]
    task_store = deal_store.read_json(package_dir / "phases" / "ic_agent_tasks.json", {})
    task = next(item for item in task_store["tasks"] if item["agent_id"] == AGENT_ID)
    assert task["status"] == "timed_out"
    assert "wall-clock timeout" in task["failure_reason"]


def test_r1_model_task_timeout_retry_preserves_failed_attempt_lineage(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    run_ids = iter(["run-timeout-attempt-1", "run-success-attempt-2"])
    create_calls: list[str] = []
    session_ids: list[str] = []
    stop_calls: list[str] = []

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        run_id = next(run_ids)
        create_calls.append(run_id)
        session_ids.append(session_id or "")
        return run_id

    async def fake_collect(run_id, *, profile, timeout=None):
        if run_id == "run-timeout-attempt-1":
            await asyncio.Event().wait()
        return json.dumps(_model_output(), ensure_ascii=False)

    async def fake_stop(run_id, *, profile):
        stop_calls.append(run_id)
        return {"stopped": True}

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "stop_run", fake_stop)

    with pytest.raises(ic_phase_orchestrator.ICTaskWallClockTimeout):
        asyncio.run(
            ic_phase_orchestrator.run_r1_model_task(
                package_dir,
                agent_id=AGENT_ID,
                receipt=_receipt(),
                timeout=0.02,
            )
        )

    result = asyncio.run(
        ic_phase_orchestrator.run_r1_model_task(
            package_dir,
            agent_id=AGENT_ID,
            receipt=_receipt(),
            timeout=2,
        )
    )

    task = result["execution"]["task"]
    assert create_calls == ["run-timeout-attempt-1", "run-success-attempt-2"]
    assert session_ids[0].endswith("-attempt-1")
    assert session_ids[1].endswith("-attempt-2")
    assert session_ids[0] != session_ids[1]
    assert stop_calls == ["run-timeout-attempt-1"]
    assert task["status"] == "succeeded"
    assert task["task_claim"]["attempt"] == 2
    assert task["hermes_run_ids"] == ["run-success-attempt-2"]
    assert task["attempt_history"] == [
        {
            "lease_attempt": 1,
            "terminal_status": "timed_out",
            "started_at": task["attempt_history"][0]["started_at"],
            "terminal_at": task["attempt_history"][0]["terminal_at"],
            "hermes_run_id": "run-timeout-attempt-1",
            "hermes_run_ids": ["run-timeout-attempt-1"],
            "output_artifact_path": None,
            "output_artifact_paths": [],
            "output_artifact_hash": None,
            "output_artifact_hashes": {},
                "contract_validation": {
                    "passed": False,
                    "output_schema": "siq_ic_expert_report_v2",
                    "error_type": "ICTaskWallClockTimeout",
                },
                "model_execution_audit": task["attempt_history"][0][
                    "model_execution_audit"
                ],
                "error": task["attempt_history"][0]["error"],
            }
        ]
    prior_model_audit = task["attempt_history"][0]["model_execution_audit"]
    assert prior_model_audit["runtime_metadata_status"] == "unverified"
    assert prior_model_audit["attempts"][0]["terminal_status"] == "unavailable"
    assert prior_model_audit["attempts"][0]["hermes_run_id"] == "run-timeout-attempt-1"
    assert task["attempt_history"][0]["started_at"]
    assert task["attempt_history"][0]["terminal_at"]
    assert "wall-clock timeout" in task["attempt_history"][0]["error"]


def test_r1_model_task_reuses_verified_success_without_new_hermes_run(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    create_calls: list[str] = []

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        create_calls.append(profile)
        return "run-reusable-success"

    async def fake_collect(run_id, *, profile, timeout=None):
        return json.dumps(_model_output(), ensure_ascii=False)

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    first = asyncio.run(
        ic_phase_orchestrator.run_r1_model_task(
            package_dir,
            agent_id=AGENT_ID,
            receipt=_receipt(),
        )
    )
    second = asyncio.run(
        ic_phase_orchestrator.run_r1_model_task(
            package_dir,
            agent_id=AGENT_ID,
            receipt=_receipt(),
        )
    )

    assert create_calls == [AGENT_ID]
    assert second["execution"]["reused"] is True
    assert second["execution"]["task"] == first["execution"]["task"]
    assert second["execution"]["output"] == first["execution"]["output"]
    claims = deal_store.read_json(package_dir / ic_phase_orchestrator.TASK_LEASE_PATH, {})["claims"]
    assert claims[0]["attempt"] == 1
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})
    assert any(event["event_type"] == "ic_phase_hermes_task_reused" for event in audit["events"])


def test_r1_model_task_reuse_fails_closed_when_raw_artifact_is_tampered(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    create_calls: list[str] = []

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        create_calls.append(profile)
        return "run-tamper-source"

    async def fake_collect(run_id, *, profile, timeout=None):
        return json.dumps(_model_output(), ensure_ascii=False)

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    first = asyncio.run(
        ic_phase_orchestrator.run_r1_model_task(
            package_dir,
            agent_id=AGENT_ID,
            receipt=_receipt(),
        )
    )
    raw_path = package_dir / first["execution"]["task"]["output_artifact_path"]
    raw_path.write_text('{"tampered": true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="failed reuse verification.*sha256_mismatch"):
        asyncio.run(
            ic_phase_orchestrator.run_r1_model_task(
                package_dir,
                agent_id=AGENT_ID,
                receipt=_receipt(),
            )
        )

    assert create_calls == [AGENT_ID]


def test_r2_model_calls_five_profiles_and_persists_validated_delta_reports(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    r1_reports = {
        agent_id: {
            "report_id": f"ICRPT-{agent_id.removeprefix('siq_ic_').replace('_', '-').upper()}-R1-0001",
            "agent_id": agent_id,
            "score": 75,
            "revision": 1,
            "claims": [],
        }
        for agent_id in R2_AGENTS
    }
    _write_json(package_dir / "phases" / "r1_reports.json", r1_reports)
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {"schema_version": "siq_ic_disputes_v1", "deal_id": DEAL_ID, "disputes": []},
    )
    receipts = {agent_id: _phase_receipt(agent_id, "R2") for agent_id in R2_AGENTS}
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v2",
            "deal_id": DEAL_ID,
            "agents": receipts,
            "by_agent_phase": {agent_id: {"R2": receipt} for agent_id, receipt in receipts.items()},
        },
    )
    calls: list[str] = []

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        calls.append(profile)
        return f"run-r2-{profile}"

    async def fake_collect(run_id, *, profile, timeout=None):
        return json.dumps(_r2_model_output(profile), ensure_ascii=False)

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    result = asyncio.run(ic_phase_orchestrator.run_r2_model(package_dir))

    assert calls == list(R2_AGENTS)
    assert result["hermes_called"] is True
    assert result["workflow_advanced"] is True
    assert set(result["reports"]) == set(R2_AGENTS)
    for report in result["reports"].values():
        assert report["schema_version"] == "siq_ic_expert_report_v2"
        assert report["revision_contract_schema_version"] == "siq_ic_r2_revision_v1"
        assert report["score_change"] == 0
        assert report["generation_mode"] == "model"
        assert report["startup_retrieval_gate"]["private_background_ready"] is True
    handoff_store = deal_store.read_json(package_dir / "phases" / "ic_agent_handoffs.json", {})
    r2_handoffs = [item for item in handoff_store["handoffs"] if item["phase"] == "R2"]
    assert len(r2_handoffs) == len(R2_AGENTS)
    for handoff in r2_handoffs:
        sidecar = handoff_store["payloads"][handoff["handoff_id"]]
        assert sidecar["payload"]["relevant_peer_claims"] == []
        assert sidecar["payload"]["new_evidence_ids"] == []
        assert sidecar["payload"]["peer_claim_filter"]["selected_claim_ids"] == []
        assert len(sidecar["reports"]) == 1


def test_r2_model_resume_reuses_completed_profiles_and_retries_only_failed_profile(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    r1_reports = {
        agent_id: {
            "report_id": f"ICRPT-{agent_id.removeprefix('siq_ic_').replace('_', '-').upper()}-R1-0001",
            "agent_id": agent_id,
            "score": 75,
            "revision": 1,
            "claims": [],
        }
        for agent_id in R2_AGENTS
    }
    _write_json(package_dir / "phases" / "r1_reports.json", r1_reports)
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {"schema_version": "siq_ic_disputes_v1", "deal_id": DEAL_ID, "disputes": []},
    )
    receipts = {agent_id: _phase_receipt(agent_id, "R2") for agent_id in R2_AGENTS}
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v2",
            "deal_id": DEAL_ID,
            "agents": receipts,
            "by_agent_phase": {agent_id: {"R2": receipt} for agent_id, receipt in receipts.items()},
        },
    )
    finance = "siq_ic_finance_auditor"
    first_create_calls: list[str] = []
    stopped: list[str] = []

    async def first_create_run(prompt, history, *, profile, session_id=None):
        first_create_calls.append(profile)
        return f"run-r2-first-{profile}"

    async def first_collect(run_id, *, profile, timeout=None):
        if profile == finance:
            await asyncio.Event().wait()
        return json.dumps(_r2_model_output(profile), ensure_ascii=False)

    async def fake_stop(run_id, *, profile):
        stopped.append(profile)
        return {"stopped": True}

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", first_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", first_collect)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "stop_run", fake_stop)

    with pytest.raises(ic_phase_orchestrator.ICTaskWallClockTimeout):
        asyncio.run(ic_phase_orchestrator.run_r2_model(package_dir, timeout=0.05))

    assert first_create_calls == [*R2_AGENTS[:3]]
    assert stopped == [finance]
    before = deal_store.read_json(package_dir / ic_phase_orchestrator.TASK_STORE_PATH, {})
    before_by_agent = {
        item["agent_id"]: deepcopy(item)
        for item in before["tasks"]
        if item.get("round_name") == "R2"
    }
    assert before_by_agent[R2_AGENTS[0]]["status"] == "succeeded"
    assert before_by_agent[R2_AGENTS[1]]["status"] == "succeeded"
    assert before_by_agent[finance]["status"] == "timed_out"

    resumed_create_calls: list[str] = []

    async def resumed_create_run(prompt, history, *, profile, session_id=None):
        resumed_create_calls.append(profile)
        return f"run-r2-resumed-{profile}"

    async def resumed_collect(run_id, *, profile, timeout=None):
        return json.dumps(_r2_model_output(profile), ensure_ascii=False)

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", resumed_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", resumed_collect)
    result = asyncio.run(ic_phase_orchestrator.run_r2_model(package_dir, timeout=2))

    assert resumed_create_calls == [finance, *R2_AGENTS[3:]]
    assert result["workflow_advanced"] is True
    assert [item["reused"] for item in result["task_results"][:2]] == [True, True]
    after = deal_store.read_json(package_dir / ic_phase_orchestrator.TASK_STORE_PATH, {})
    after_by_agent = {
        item["agent_id"]: item
        for item in after["tasks"]
        if item.get("round_name") == "R2"
    }
    for agent_id in R2_AGENTS[:2]:
        assert after_by_agent[agent_id]["hermes_run_id"] == before_by_agent[agent_id]["hermes_run_id"]
        assert after_by_agent[agent_id]["task_claim"]["attempt"] == 1
    finance_task = after_by_agent[finance]
    assert finance_task["task_claim"]["attempt"] == 2
    assert finance_task["attempt_history"][0]["terminal_status"] == "timed_out"
    assert finance_task["attempt_history"][0]["hermes_run_id"] == f"run-r2-first-{finance}"


def test_r2_peer_claim_filter_is_role_relevant_and_keeps_critical_claims_for_risk(tmp_path):
    package_dir = _package(tmp_path)
    hinted_evidence_id = "EVID-DEAL-IC-MODEL-001-FINANCE-HINT"
    evidence = [
        {
            "evidence_id": EVIDENCE_ID,
            "deal_id": DEAL_ID,
            "source_id": "PM:SOURCE-001",
            "role_hints": [],
        },
        {
            "evidence_id": hinted_evidence_id,
            "deal_id": DEAL_ID,
            "source_id": "PM:SOURCE-001",
            "role_hints": ["siq_ic_finance_auditor"],
        },
    ]
    (package_dir / "evidence" / "evidence_items.ndjson").write_text(
        "\n".join(json.dumps(item) for item in evidence) + "\n",
        encoding="utf-8",
    )
    reports = {
        "siq_ic_strategist": _peer_report(
            "siq_ic_strategist",
            [_peer_claim("CLM-STRATEGY-POLICY-001", topic="policy_window")],
        ),
        "siq_ic_sector_expert": _peer_report(
            "siq_ic_sector_expert",
            [
                _peer_claim(
                    "CLM-SECTOR-TECH-001",
                    topic="technology_route",
                    evidence_id=hinted_evidence_id,
                )
            ],
        ),
        "siq_ic_finance_auditor": _peer_report("siq_ic_finance_auditor", []),
        "siq_ic_legal_scanner": _peer_report(
            "siq_ic_legal_scanner",
            [_peer_claim("CLM-LEGAL-PATENT-001", topic="patent_ownership", impact="critical")],
        ),
        "siq_ic_risk_controller": _peer_report(
            "siq_ic_risk_controller",
            [_peer_claim("CLM-RISK-CASH-001", topic="cash_downside")],
        ),
    }

    finance_claims, finance_filter = ic_phase_orchestrator._r2_relevant_peer_claims(
        package_dir,
        agent_id="siq_ic_finance_auditor",
        r1_reports=reports,
    )
    risk_claims, risk_filter = ic_phase_orchestrator._r2_relevant_peer_claims(
        package_dir,
        agent_id="siq_ic_risk_controller",
        r1_reports=reports,
    )

    assert [item["claim_id"] for item in finance_claims] == [
        "CLM-SECTOR-TECH-001",
        "CLM-RISK-CASH-001",
    ]
    assert finance_claims[0]["source_agent_id"] == "siq_ic_sector_expert"
    assert finance_claims[0]["source_report_id"] == reports["siq_ic_sector_expert"]["report_id"]
    assert finance_claims[0]["selection_reasons"] == ["evidence_role_hint"]
    assert finance_claims[1]["selection_reasons"] == ["role_topic"]
    assert finance_filter["excluded_claim_ids"] == [
        "CLM-STRATEGY-POLICY-001",
        "CLM-LEGAL-PATENT-001",
    ]
    assert [item["claim_id"] for item in risk_claims] == ["CLM-LEGAL-PATENT-001"]
    assert risk_claims[0]["selection_reasons"] == ["risk_critical_cross_role"]
    assert risk_filter["schema_version"] == "siq_ic_r2_peer_claim_filter_v1"
    peer_report_views = ic_phase_orchestrator._r2_filtered_peer_reports(reports, finance_claims)
    assert [item["agent_id"] for item in peer_report_views] == [
        "siq_ic_sector_expert",
        "siq_ic_risk_controller",
    ]
    assert all(set(item) == {"report_id", "agent_id", "claims"} for item in peer_report_views)


def test_r2_peer_claim_filter_fails_closed_on_report_identity_mismatch(tmp_path):
    package_dir = _package(tmp_path)
    reports = {
        agent_id: _peer_report(agent_id, [])
        for agent_id in R2_AGENTS
    }
    reports["siq_ic_legal_scanner"]["agent_id"] = "siq_ic_strategist"

    with pytest.raises(ValueError, match="peer_report_agent_identity_mismatch:siq_ic_legal_scanner"):
        ic_phase_orchestrator._r2_relevant_peer_claims(
            package_dir,
            agent_id="siq_ic_finance_auditor",
            r1_reports=reports,
        )


def test_r2_new_evidence_delta_requires_a_new_snapshot_source_and_role_match(tmp_path):
    package_dir = _package(tmp_path)
    old_evidence_id = EVIDENCE_ID
    finance_evidence_id = "EVID-DEAL-IC-MODEL-001-NEW-FINANCE"
    legal_evidence_id = "EVID-DEAL-IC-MODEL-001-NEW-LEGAL"
    evidence = [
        {
            "evidence_id": old_evidence_id,
            "source_id": "PM:SOURCE-OLD",
            "role_hints": ["siq_ic_finance_auditor"],
        },
        {
            "evidence_id": finance_evidence_id,
            "source_id": "PM:SOURCE-NEW",
            "role_hints": ["siq_ic_finance_auditor"],
        },
        {
            "evidence_id": legal_evidence_id,
            "source_id": "PM:SOURCE-NEW",
            "role_hints": ["siq_ic_legal_scanner"],
        },
    ]
    (package_dir / "evidence" / "evidence_items.ndjson").write_text(
        "\n".join(json.dumps(item) for item in evidence) + "\n",
        encoding="utf-8",
    )

    def receipt(agent_id: str, round_name: str, snapshot: str, sources: list[str], hits: list[str]) -> dict:
        return {
            "receipt_id": f"receipt-{agent_id}-{round_name}",
            "agent_id": agent_id,
            "round_name": round_name,
            "evidence_snapshot_hash": snapshot,
            "source_ids": sources,
            "project_evidence_hits": [{"evidence_id": item} for item in hits],
        }

    old_snapshot = "a" * 64
    new_snapshot = "b" * 64
    finance_r1 = receipt(
        "siq_ic_finance_auditor",
        "R1",
        old_snapshot,
        ["PM:SOURCE-OLD"],
        [old_evidence_id],
    )
    finance_r2 = receipt(
        "siq_ic_finance_auditor",
        "R2",
        new_snapshot,
        ["PM:SOURCE-OLD", "PM:SOURCE-NEW"],
        [old_evidence_id, finance_evidence_id, legal_evidence_id],
    )
    risk_r1 = receipt(
        "siq_ic_risk_controller",
        "R1",
        old_snapshot,
        ["PM:SOURCE-OLD"],
        [old_evidence_id],
    )
    risk_r2 = receipt(
        "siq_ic_risk_controller",
        "R2",
        new_snapshot,
        ["PM:SOURCE-OLD", "PM:SOURCE-NEW"],
        [old_evidence_id, finance_evidence_id, legal_evidence_id],
    )
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "by_agent_phase": {
                "siq_ic_finance_auditor": {"R1": finance_r1, "R2": finance_r2},
                "siq_ic_risk_controller": {"R1": risk_r1, "R2": risk_r2},
            }
        },
    )

    finance_delta = ic_phase_orchestrator._r2_new_evidence_delta(
        package_dir,
        agent_id="siq_ic_finance_auditor",
        current_receipt=finance_r2,
    )
    risk_delta = ic_phase_orchestrator._r2_new_evidence_delta(
        package_dir,
        agent_id="siq_ic_risk_controller",
        current_receipt=risk_r2,
    )
    unchanged_delta = ic_phase_orchestrator._r2_new_evidence_delta(
        package_dir,
        agent_id="siq_ic_finance_auditor",
        current_receipt={**finance_r2, "evidence_snapshot_hash": old_snapshot},
    )

    assert finance_delta["snapshot_changed"] is True
    assert finance_delta["new_source_ids"] == ["PM:SOURCE-NEW"]
    assert finance_delta["new_evidence_ids"] == [finance_evidence_id]
    assert risk_delta["new_evidence_ids"] == [finance_evidence_id, legal_evidence_id]
    assert unchanged_delta["snapshot_changed"] is False
    assert unchanged_delta["new_source_ids"] == []
    assert unchanged_delta["new_evidence_ids"] == []


def test_r15_model_materializes_shared_dispute_contract(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    chairman = "siq_ic_chairman"
    receipt = _phase_receipt(chairman, "R1.5")
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v2",
            "deal_id": DEAL_ID,
            "agents": {chairman: receipt},
            "by_agent_phase": {chairman: {"R1.5": receipt}},
        },
    )
    dispute_id = f"DISP-{DEAL_ID}-001"
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": DEAL_ID,
            "disputes": [
                {
                    "dispute_id": dispute_id,
                    "topic": "Recommendation divergence",
                    "dimension": "committee_alignment",
                    "severity": "high",
                    "resolved": False,
                    "agent_ids": ["siq_ic_strategist", "siq_ic_risk_controller"],
                    "evidence_ids": [EVIDENCE_ID],
                    "positions": [
                        {"agent_id": "siq_ic_strategist", "recommendation": "support", "evidence_ids": [EVIDENCE_ID]},
                        {"agent_id": "siq_ic_risk_controller", "recommendation": "reject", "evidence_ids": [EVIDENCE_ID]},
                    ],
                    "required_followups": [],
                }
            ],
        },
    )

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        assert profile == chairman
        assert '"output_schema": "siq_ic_r1_5_chairman_rulings_v2"' in prompt
        return "run-r15-chairman"

    async def fake_collect(run_id, *, profile, timeout=None):
        return json.dumps(
            {
                "rulings": [
                    {
                        "dispute_id": dispute_id,
                        "ruling": "synthesize",
                        "rationale": "The Evidence supports a conditional synthesis.",
                        "required_followups": ["Add a downside covenant."],
                        "evidence_ids": [EVIDENCE_ID],
                        "counter_evidence_ids": [],
                        "accepted_claim_ids": [],
                        "rejected_claim_ids": [],
                        "decision_impact": "material",
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    result = asyncio.run(ic_phase_orchestrator.run_r15_model(package_dir))

    assert result["hermes_called"] is True
    assert result["rulings"][0]["schema_version"] == "siq_ic_r1_5_dispute_v1"
    assert result["rulings"][0]["generation_mode"] == "model"
    assert result["rulings"][0]["dispute_id"] == dispute_id
    assert len(result["rulings"][0]["positions"]) == 2
    assert result["rulings"][0]["evidence_ids"] == [EVIDENCE_ID]
    persisted = deal_store.read_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {},
    )["disputes"][0]
    assert persisted["evidence_snapshot_hash"] == SNAPSHOT
    assert persisted["chairman_ruling"]["evidence_snapshot_hash"] == SNAPSHOT
    assert (
        persisted["chairman_ruling"]["submission_schema_version"]
        == "siq_ic_r1_5_dispute_v1"
    )
    assert persisted["chairman_ruling"]["source_created_at"] == result["rulings"][0][
        "created_at"
    ]


def test_r15_needs_more_evidence_does_not_advance_workflow(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    chairman = "siq_ic_chairman"
    receipt = _phase_receipt(chairman, "R1.5")
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v2",
            "deal_id": DEAL_ID,
            "agents": {chairman: receipt},
            "by_agent_phase": {chairman: {"R1.5": receipt}},
        },
    )
    dispute_id = f"DISP-{DEAL_ID}-EVIDENCE-001"
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": DEAL_ID,
            "disputes": [
                {
                    "dispute_id": dispute_id,
                    "topic": "Customer concentration cannot yet be verified",
                    "dimension": "financial_quality",
                    "severity": "high",
                    "resolved": False,
                    "agent_ids": ["siq_ic_finance_auditor", "siq_ic_risk_controller"],
                    "evidence_ids": [EVIDENCE_ID],
                    "positions": [
                        {
                            "agent_id": "siq_ic_finance_auditor",
                            "recommendation": "conditional_support",
                            "evidence_ids": [EVIDENCE_ID],
                        },
                        {
                            "agent_id": "siq_ic_risk_controller",
                            "recommendation": "insufficient_evidence",
                            "evidence_ids": [EVIDENCE_ID],
                        },
                    ],
                    "required_followups": [],
                }
            ],
        },
    )

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        assert profile == chairman
        assert '"output_schema": "siq_ic_r1_5_chairman_rulings_v2"' in prompt
        return "run-r15-needs-evidence"

    async def fake_collect(run_id, *, profile, timeout=None):
        return json.dumps(
            {
                "rulings": [
                    {
                        "dispute_id": dispute_id,
                        "ruling": "needs_more_evidence",
                        "rationale": "The current Evidence does not disclose auditable customer concentration details.",
                        "required_followups": ["Obtain and verify the top-ten customer revenue ledger."],
                        "evidence_ids": [EVIDENCE_ID],
                        "counter_evidence_ids": [],
                        "accepted_claim_ids": [],
                        "rejected_claim_ids": [],
                        "decision_impact": "critical",
                    }
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    result = asyncio.run(ic_phase_orchestrator.run_r15_model(package_dir))

    assert result["status"] == "needs_more_evidence"
    assert result["submission"]["can_proceed_to_r2"] is False
    assert result["rulings"][0]["ruling"] == "needs_more_evidence"
    assert result["rulings"][0]["resolved"] is False
    assert result["rulings"][0]["required_followups"] == [
        "Obtain and verify the top-ten customer revenue ledger."
    ]
    assert result["workflow_advanced"] is False
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {})
    assert workflow["current_phase"] == "R1.5"
    assert workflow["phases"]["R1.5"]["status"] != "completed"


def test_r15_no_unresolved_disputes_reports_completed_workflow(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": DEAL_ID,
            "disputes": [],
        },
    )
    _write_json(
        package_dir / "phases" / "workflow_state.json",
        {
            "schema_version": "siq_deal_workflow_state_v1",
            "deal_id": DEAL_ID,
            "current_phase": "R1.5",
            "status": "r1_5_clear",
            "phases": {"R1.5": {"status": "completed", "unresolved_count": 0}},
        },
    )
    monkeypatch.setattr(
        ic_phase_orchestrator.deal_disputes,
        "build_chairman_ruling_task",
        lambda *args, **kwargs: {"disputes": []},
    )
    monkeypatch.setattr(
        ic_phase_orchestrator.deal_disputes,
        "summarize_deal_disputes",
        lambda *args, **kwargs: {
            "status": "pass",
            "counts": {"disputes": 0, "resolved": 0, "unresolved": 0, "rulings": 0},
        },
    )

    result = asyncio.run(ic_phase_orchestrator.run_r15_model(package_dir))

    assert result["status"] == "completed"
    assert result["generation_mode"] == "no_unresolved_disputes"
    assert result["hermes_called"] is False
    assert result["workflow_advanced"] is True
    assert result["workflow"]["phases"]["R1.5"]["status"] == "completed"


def test_r3_model_runs_short_red_blue_debate_and_shared_contract(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    chairman = "siq_ic_chairman"
    receipts = {
        agent_id: _phase_receipt(agent_id, "R3")
        for agent_id in (*R2_AGENTS, chairman)
    }
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v2",
            "deal_id": DEAL_ID,
            "agents": receipts,
            "by_agent_phase": {agent_id: {"R3": receipt} for agent_id, receipt in receipts.items()},
        },
    )
    r2_reports = {
        agent_id: {
            "report_id": f"ICRPT-{agent_id.removeprefix('siq_ic_').replace('_', '-').upper()}-R2-0001",
            "agent_id": agent_id,
            "recommendation": "reject" if agent_id == "siq_ic_risk_controller" else "support",
            "score": 45 if agent_id == "siq_ic_risk_controller" else 78,
            "r2_score": 45 if agent_id == "siq_ic_risk_controller" else 78,
            "claims": [],
            "evidence_ids": [EVIDENCE_ID],
            "remaining_questions": [],
            "challenged_rulings": [],
            "risk_flags": [],
        }
        for agent_id in R2_AGENTS
    }
    _write_json(package_dir / "phases" / "r2_reports.json", r2_reports)
    dispute_id = f"DISP-{DEAL_ID}-R3-001"
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": DEAL_ID,
            "disputes": [
                {
                    "dispute_id": dispute_id,
                    "topic": "Downside protection",
                    "dimension": "risk",
                    "severity": "medium",
                    "resolved": False,
                    "evidence_ids": [EVIDENCE_ID],
                    "positions": [
                        {"agent_id": "siq_ic_risk_controller", "recommendation": "reject", "score": 45},
                        {"agent_id": "siq_ic_strategist", "recommendation": "support", "score": 78},
                    ],
                }
            ],
        },
    )
    _write_json(
        package_dir / "evidence" / "evidence_quality_report.json",
        {"status": "pass", "gate_status": "pass"},
    )
    calls: list[str] = []

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        expected_schema = (
            "siq_ic_r3_debate_verdict_v1"
            if profile == chairman
            else "siq_ic_r3_debate_turn_v1"
        )
        assert f'"output_schema": "{expected_schema}"' in prompt
        calls.append(profile)
        return f"run-r3-{len(calls)}-{profile}"

    async def fake_collect(run_id, *, profile, timeout=None):
        red_id = ic_phase_orchestrator._argument_id(dispute_id, "red_thesis", 1)
        blue_id = ic_phase_orchestrator._argument_id(dispute_id, "blue_defense", 2)
        if profile == "siq_ic_risk_controller":
            return json.dumps(
                {
                    "argument": "Downside protection is insufficient under the current Evidence.",
                    "claim_ids": [],
                    "evidence_ids": [EVIDENCE_ID],
                    "responds_to_argument_ids": [],
                    "unanswered_points": ["Covenant trigger remains open."],
                }
            )
        if profile == "siq_ic_strategist":
            return json.dumps(
                {
                    "argument": "A conditional covenant answers the downside concern.",
                    "claim_ids": [],
                    "evidence_ids": [EVIDENCE_ID],
                    "responds_to_argument_ids": [red_id],
                    "unanswered_points": [],
                }
            )
        return json.dumps(
            {
                "outcome": "synthesize",
                "rationale": "Proceed only with the proposed downside covenant.",
                "accepted_argument_ids": [blue_id],
                "rejected_argument_ids": [red_id],
                "evidence_ids": [EVIDENCE_ID],
                "decision_impact": "material",
                "required_followups": ["Draft the covenant."],
            }
        )

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    result = asyncio.run(ic_phase_orchestrator.run_r3_model(package_dir))

    assert calls == ["siq_ic_risk_controller", "siq_ic_strategist", chairman]
    assert result["mode"] == "short"
    assert result["payload"]["plan"]["schema_version"] == "siq_ic_r3_plan_v1"
    debate = result["payload"]["debates"][0]
    assert debate["schema_version"] == "siq_ic_r3_debate_v1"
    assert [turn["argument_id"] for turn in debate["rounds"]] == [
        ic_phase_orchestrator._argument_id(dispute_id, "red_thesis", 1),
        ic_phase_orchestrator._argument_id(dispute_id, "blue_defense", 2),
    ]
    assert debate["status"] == "resolved"


def test_r3_model_runs_full_four_turn_debate_with_response_chain(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    chairman = "siq_ic_chairman"
    receipts = {
        agent_id: _phase_receipt(agent_id, "R3")
        for agent_id in (*R2_AGENTS, chairman)
    }
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v2",
            "deal_id": DEAL_ID,
            "agents": receipts,
            "by_agent_phase": {agent_id: {"R3": receipt} for agent_id, receipt in receipts.items()},
        },
    )
    r2_reports = {
        agent_id: {
            "report_id": f"ICRPT-{agent_id.removeprefix('siq_ic_').replace('_', '-').upper()}-R2-0001",
            "agent_id": agent_id,
            "recommendation": "reject" if agent_id == "siq_ic_risk_controller" else "support",
            "score": 40 if agent_id == "siq_ic_risk_controller" else 80,
            "r2_score": 40 if agent_id == "siq_ic_risk_controller" else 80,
            "claims": [],
            "evidence_ids": [EVIDENCE_ID],
            "remaining_questions": [],
            "challenged_rulings": [],
            "risk_flags": [],
        }
        for agent_id in R2_AGENTS
    }
    _write_json(package_dir / "phases" / "r2_reports.json", r2_reports)
    dispute_id = f"DISP-{DEAL_ID}-R3-FULL-001"
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": DEAL_ID,
            "disputes": [
                {
                    "dispute_id": dispute_id,
                    "topic": "Critical downside protection",
                    "dimension": "risk",
                    "severity": "high",
                    "resolved": False,
                    "evidence_ids": [EVIDENCE_ID],
                    "positions": [
                        {
                            "agent_id": "siq_ic_risk_controller",
                            "recommendation": "reject",
                            "score": 40,
                            "evidence_ids": [EVIDENCE_ID],
                        },
                        {
                            "agent_id": "siq_ic_strategist",
                            "recommendation": "support",
                            "score": 80,
                            "evidence_ids": [EVIDENCE_ID],
                        },
                    ],
                }
            ],
        },
    )
    _write_json(
        package_dir / "evidence" / "evidence_quality_report.json",
        {"status": "pass", "gate_status": "pass"},
    )
    turn_ids = [
        ic_phase_orchestrator._argument_id(dispute_id, "red_thesis", 1),
        ic_phase_orchestrator._argument_id(dispute_id, "blue_defense", 2),
        ic_phase_orchestrator._argument_id(dispute_id, "red_rebuttal", 3),
        ic_phase_orchestrator._argument_id(dispute_id, "blue_final_response", 4),
    ]
    expected_calls = [
        "siq_ic_risk_controller",
        "siq_ic_strategist",
        "siq_ic_risk_controller",
        "siq_ic_strategist",
        chairman,
    ]
    calls: list[str] = []

    async def fake_create_run(prompt, history, *, profile, session_id=None):
        expected_schema = (
            "siq_ic_r3_debate_verdict_v1"
            if profile == chairman
            else "siq_ic_r3_debate_turn_v1"
        )
        assert f'"output_schema": "{expected_schema}"' in prompt
        calls.append(profile)
        return f"run-r3-full-{len(calls)}-{profile}"

    async def fake_collect(run_id, *, profile, timeout=None):
        call_index = len(calls) - 1
        if profile == chairman:
            return json.dumps(
                {
                    "outcome": "synthesize",
                    "rationale": "The final covenant response resolves the high-severity downside dispute.",
                    "accepted_argument_ids": [turn_ids[3]],
                    "rejected_argument_ids": [turn_ids[0]],
                    "evidence_ids": [EVIDENCE_ID],
                    "decision_impact": "critical",
                    "required_followups": ["Add the downside covenant to closing conditions."],
                }
            )
        arguments = [
            "The current terms do not protect the critical downside.",
            "A measurable covenant can protect the downside.",
            "The proposed covenant still lacks an enforceable trigger.",
            "The final covenant adds an enforceable evidence-linked trigger.",
        ]
        return json.dumps(
            {
                "argument": arguments[call_index],
                "claim_ids": [],
                "evidence_ids": [EVIDENCE_ID],
                "responds_to_argument_ids": [] if call_index == 0 else [turn_ids[call_index - 1]],
                "unanswered_points": ["Trigger definition remains open."] if call_index < 3 else [],
            }
        )

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    result = asyncio.run(ic_phase_orchestrator.run_r3_model(package_dir))

    assert calls == expected_calls
    assert result["mode"] == "full"
    assert result["workflow_advanced"] is True
    assert result["payload"]["plan"]["schema_version"] == "siq_ic_r3_plan_v1"
    assert result["payload"]["plan"]["estimated_rounds"] == 4
    debate = result["payload"]["debates"][0]
    assert debate["schema_version"] == "siq_ic_r3_debate_v1"
    assert [turn["argument_id"] for turn in debate["rounds"]] == turn_ids
    assert [turn["responds_to_argument_ids"] for turn in debate["rounds"]] == [
        [],
        [turn_ids[0]],
        [turn_ids[1]],
        [turn_ids[2]],
    ]
    assert debate["chairman_verdict"]["accepted_argument_ids"] == [turn_ids[3]]
    assert debate["chairman_verdict"]["rejected_argument_ids"] == [turn_ids[0]]
    assert debate["status"] == "resolved"


def _prepare_r4_inputs(package_dir) -> None:
    chairman = "siq_ic_chairman"
    r1_agents = (*R2_AGENTS, chairman)
    _write_json(
        package_dir / "phases" / "r1_reports.json",
        {
            agent_id: _full_expert_report(
                agent_id,
                "R1B" if agent_id in {"siq_ic_risk_controller", chairman} else "R1A",
            )
            for agent_id in r1_agents
        },
    )
    _write_json(
        package_dir / "phases" / "r2_reports.json",
        {agent_id: _full_expert_report(agent_id, "R2", revision=2) for agent_id in R2_AGENTS},
    )
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {"schema_version": "siq_ic_disputes_v1", "deal_id": DEAL_ID, "disputes": []},
    )
    _write_json(
        package_dir / "phases" / "r3_reports.json",
        {
            "schema_version": "siq_ic_r3_debate_bundle_v2",
            "deal_id": DEAL_ID,
            "mode": "short",
            "topics": [],
            "debates": [],
            "blocking": False,
            "blocking_topic_ids": [],
            "evidence_snapshot_hash": SNAPSHOT,
        },
    )
    _write_json(
        package_dir / "evidence" / "evidence_quality_report.json",
        {"status": "pass", "gate_status": "pass", "counts": {"items": 1}},
    )
    receipt = _phase_receipt(chairman, "R4")
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v2",
            "deal_id": DEAL_ID,
            "agents": {chairman: receipt},
            "by_agent_phase": {chairman: {"R4": receipt}},
        },
    )


def _install_interrupted_r4_factcheck_runtime(monkeypatch) -> dict:
    state = {
        "chairman_create_calls": 0,
        "factcheck_create_calls": 0,
        "factcheck_prompts": [],
    }

    async def fake_create_run(prompt, history, *, profile, session_id=None, instructions=None):
        if profile == "siq_ic_chairman":
            state["chairman_create_calls"] += 1
            return "run-r4-resume-chairman"
        assert profile == "siq_factchecker"
        state["factcheck_create_calls"] += 1
        state["factcheck_prompts"].append(prompt)
        if state["factcheck_create_calls"] == 1:
            raise RuntimeError("injected factcheck startup failure")
        return "run-r4-resume-factcheck"

    async def fake_collect(run_id, *, profile, timeout=None):
        if profile == "siq_ic_chairman":
            return json.dumps(_r4_model_output(), ensure_ascii=False)
        assert profile == "siq_factchecker"
        return json.dumps(
            {
                "schema_version": "siq_ic_report_factcheck_v1",
                "status": "pass",
                "claim_checks": [],
                "numeric_checks": [],
                "citation_checks": [],
                "contradictions": [],
                "unsupported_claims": [],
                "required_repairs": [],
            }
        )

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    return state


def test_r4_model_resume_reuses_bound_draft_identity_without_rerunning_chairman(
    monkeypatch, tmp_path
):
    package_dir = _package(tmp_path)
    _prepare_r4_inputs(package_dir)
    state = _install_interrupted_r4_factcheck_runtime(monkeypatch)

    with pytest.raises(RuntimeError, match="injected factcheck startup failure"):
        asyncio.run(ic_phase_orchestrator.run_r4_model(package_dir))

    draft_path = package_dir / "decision" / "decision_draft.json"
    first_draft = deal_store.read_json(draft_path, {})
    task = ic_phase_orchestrator._stored_task(package_dir, first_draft["task_id"])
    task = ic_phase_orchestrator.ic_task_contracts.validate_agent_task(task)
    identity = task["r4_decision_identity"]
    assert identity["decision_sha256"] == ic_phase_orchestrator.payload_digest(first_draft)
    assert identity["created_at"] == first_draft["created_at"]
    assert identity["updated_at"] == first_draft["updated_at"]

    result = asyncio.run(ic_phase_orchestrator.run_r4_model(package_dir))

    assert result["status"] == "completed"
    assert state["chairman_create_calls"] == 1
    assert state["factcheck_create_calls"] == 2
    assert state["factcheck_prompts"][0] == state["factcheck_prompts"][1]
    assert result["decision"] == first_draft
    assert result["decision"]["created_at"] == first_draft["created_at"]
    assert result["decision"]["updated_at"] == first_draft["updated_at"]


def test_r4_model_resume_rejects_tampered_bound_draft(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    _prepare_r4_inputs(package_dir)
    state = _install_interrupted_r4_factcheck_runtime(monkeypatch)

    with pytest.raises(RuntimeError, match="injected factcheck startup failure"):
        asyncio.run(ic_phase_orchestrator.run_r4_model(package_dir))

    draft_path = package_dir / "decision" / "decision_draft.json"
    draft = deal_store.read_json(draft_path, {})
    draft["executive_summary"] = "tampered summary"
    deal_store.write_json(draft_path, draft)

    with pytest.raises(ValueError, match="draft failed resume verification: sha256_mismatch"):
        asyncio.run(ic_phase_orchestrator.run_r4_model(package_dir))

    assert state["chairman_create_calls"] == 1
    assert state["factcheck_create_calls"] == 1


def test_r4_model_resume_rejects_persisted_decision_identity_mismatch(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    _prepare_r4_inputs(package_dir)
    state = _install_interrupted_r4_factcheck_runtime(monkeypatch)

    with pytest.raises(RuntimeError, match="injected factcheck startup failure"):
        asyncio.run(ic_phase_orchestrator.run_r4_model(package_dir))

    draft = deal_store.read_json(package_dir / "decision" / "decision_draft.json", {})
    store_path = package_dir / ic_phase_orchestrator.TASK_STORE_PATH
    task_store = deal_store.read_json(store_path, {})
    task = next(item for item in task_store["tasks"] if item["task_id"] == draft["task_id"])
    task["r4_decision_identity"]["report_id"] = "ICRPT-TAMPERED-IDENTITY"
    deal_store.write_json(store_path, task_store)

    with pytest.raises(ValueError, match="persisted decision identity mismatch: report_id"):
        asyncio.run(ic_phase_orchestrator.run_r4_model(package_dir))

    assert state["chairman_create_calls"] == 1
    assert state["factcheck_create_calls"] == 1


def test_r4_model_factchecks_r1_and_r2_then_allows_human_confirmation(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    chairman = "siq_ic_chairman"
    r1_agents = (*R2_AGENTS, chairman)
    r1_reports = {
        agent_id: _full_expert_report(
            agent_id,
            "R1B" if agent_id in {"siq_ic_risk_controller", chairman} else "R1A",
        )
        for agent_id in r1_agents
    }
    r2_reports = {
        agent_id: _full_expert_report(agent_id, "R2", revision=2)
        for agent_id in R2_AGENTS
    }
    _write_json(package_dir / "phases" / "r1_reports.json", r1_reports)
    _write_json(package_dir / "phases" / "r2_reports.json", r2_reports)
    _write_json(
        package_dir / "phases" / "r1_5_disputes.json",
        {"schema_version": "siq_ic_disputes_v1", "deal_id": DEAL_ID, "disputes": []},
    )
    _write_json(
        package_dir / "phases" / "r3_reports.json",
        {
            "schema_version": "siq_ic_r3_debate_bundle_v2",
            "deal_id": DEAL_ID,
            "mode": "short",
            "topics": [],
            "debates": [],
            "blocking": False,
            "blocking_topic_ids": [],
            "evidence_snapshot_hash": SNAPSHOT,
        },
    )
    _write_json(
        package_dir / "evidence" / "evidence_quality_report.json",
        {"status": "pass", "gate_status": "pass", "counts": {"items": 1}},
    )
    _write_json(
        package_dir / "phases" / "r0_readiness.json",
        {
            "readiness": "ready",
            "evidence_snapshot_hash": SNAPSHOT,
            "material_completeness": {"prospectus": "ready"},
            "evidence_gaps": [],
            "blocking_reasons": [],
        },
    )
    _write_json(
        package_dir / "data_room" / "materials_manifest.json",
        {"status": "ready", "completeness": {"prospectus": "complete"}, "blocking_reasons": []},
    )
    receipt = _phase_receipt(chairman, "R4")
    _write_json(
        package_dir / "phases" / "startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v2",
            "deal_id": DEAL_ID,
            "agents": {chairman: receipt},
            "by_agent_phase": {chairman: {"R4": receipt}},
        },
    )
    calls: list[str] = []
    render_bundles: list[dict] = []
    original_render = ic_phase_orchestrator.ic_r4_report_renderer.render_r4_report

    def capture_render_bundle(bundle):
        render_bundles.append(deepcopy(bundle))
        return original_render(bundle)

    async def fake_create_run(prompt, history, *, profile, session_id=None, instructions=None):
        if profile == "siq_factchecker":
            assert "fenced SIQ primary-market IC factcheck task" in instructions
            assert "not itself an unsupported factual assertion" in instructions
            assert "do not turn future due-diligence evidence collection" in instructions
            assert "Evidence envelope 是唯一正式输入" in prompt
            assert "不得调用 terminal" in prompt
            assert "不得调用 code_execution 或任何其他工具" in prompt
            assert "Do not call code_execution or any other tool" in instructions
            assert "该披露本身不是 unsupported_claim" in prompt
            assert "pass 表示当前报告事实完整性通过" in prompt
            assert "自创 calculation_trace_id" in prompt
        calls.append(profile)
        return f"run-r4-{profile}"

    async def fake_collect(run_id, *, profile, timeout=None):
        if profile == chairman:
            return json.dumps(_r4_model_output(), ensure_ascii=False)
        assert profile == "siq_factchecker"
        output = json.dumps(
            {
                "schema_version": "siq_ic_report_factcheck_v1",
                "status": "pass",
                "claim_checks": [],
                "numeric_checks": [],
                "citation_checks": [],
                "contradictions": [],
                "unsupported_claims": [],
                "required_repairs": [],
            }
        )
        ic_phase_orchestrator.hermes_client._remember_run_terminal(
            ic_phase_orchestrator.hermes_client.RunTerminalResult(
                run_id=run_id,
                status="succeeded",
                received_text=output,
                runtime=ic_phase_orchestrator.hermes_client.RunRuntimeMetadata(
                    requested_model="siq_factchecker",
                    configured_provider="minimax-cn",
                    configured_model="MiniMax-M3",
                    effective_provider="minimax-cn",
                    effective_model="MiniMax-M3",
                    fallback_activated=False,
                ),
            )
        )
        return output

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    monkeypatch.setattr(ic_phase_orchestrator.ic_r4_report_renderer, "render_r4_report", capture_render_bundle)
    result = asyncio.run(ic_phase_orchestrator.run_r4_model(package_dir))

    assert calls == [chairman, "siq_factchecker"]
    assert result["status"] == "completed"
    assert result["decision"]["schema_version"] == "siq_ic_r4_decision_v2"
    assert result["factcheck"]["report_id"] == result["decision"]["report_id"]
    assert result["factcheck"]["evidence_snapshot_hash"] == SNAPSHOT
    factcheck_task = result["factcheck_task"]
    assert factcheck_task["report_id"] == result["decision"]["report_id"]
    assert factcheck_task["report_revision"] == result["decision"]["revision"]
    assert factcheck_task["evidence_snapshot_hash"] == SNAPSHOT
    assert factcheck_task["prompt_contract_version"] == "siq_ic_phase_prompt_v5"
    assert factcheck_task["profile_contract_version"] == "hermes_profile_authority_v1"
    assert factcheck_task["output_schema"] == "siq_ic_report_factcheck_v1"
    raw_path = package_dir / factcheck_task["output_artifact_path"]
    raw_hash = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    assert factcheck_task["output_artifact_hash"] == raw_hash
    assert factcheck_task["output_artifact_hashes"] == {
        factcheck_task["output_artifact_path"]: raw_hash,
    }
    assert factcheck_task["contract_validation"] == {
        "passed": True,
        "output_schema": "siq_ic_report_factcheck_v1",
        "artifact_schema": "siq_ic_report_factcheck_v1",
        "validated_by": "ic_phase_orchestrator",
    }
    assert factcheck_task["task_claim"]["status"] == "succeeded"
    assert factcheck_task["model_execution_audit"]["runtime_metadata_status"] == "verified"
    assert factcheck_task["model_execution_audit"]["final_runtime"]["effective"] == {
        "provider": "minimax-cn",
        "model": "MiniMax-M3",
    }
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})
    completion = next(
        event for event in audit["events"] if event["event_type"] == "ic_r4_factcheck_completed"
    )
    for field in (
        "workflow_run_id",
        "task_id",
        "report_id",
        "report_revision",
        "evidence_snapshot_hash",
        "prompt_contract_version",
        "profile_contract_version",
        "output_schema",
        "output_artifact_hashes",
        "contract_validation",
        "model_execution_audit",
    ):
        assert completion[field] == factcheck_task[field]
    assert completion["status"] == "succeeded"
    assert completion["factcheck_status"] == "pass"
    assert result["quality"]["allowed_for_human_confirmation"] is True
    assert render_bundles[0]["r0_readiness"]["readiness"] == "ready"
    assert render_bundles[0]["materials_manifest"]["status"] == "ready"
    assert render_bundles[0]["evidence_snapshot"]["snapshot_hash"] == SNAPSHOT
    confirmation = deal_decision.update_human_confirmation(
        DEAL_ID,
        status="confirmed",
        confirmed_by={"id": 7, "username": "ic-admin"},
        dry_run=False,
        wiki_root=tmp_path,
    )
    assert confirmation["human_confirmation"]["status"] == "confirmed"


def test_r4_factcheck_contract_failure_persists_raw_output_and_failure_audit(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    workflow_run = ic_phase_orchestrator.ensure_workflow_run(package_dir)
    decision = {
        "report_id": "ICRPT-FACTCHECK-FAIL-001",
        "revision": 3,
        "deal_id": DEAL_ID,
        "evidence_snapshot_hash": SNAPSHOT,
        "decision": "review",
        "claims": [],
        "background_knowledge_refs": [],
    }

    monkeypatch.setenv("SIQ_IC_FACTCHECK_REPAIR_ATTEMPTS", "0")

    async def fake_create_run(prompt, history, *, profile, session_id=None, instructions=None):
        return "run-r4-factcheck-invalid"

    async def fake_collect(run_id, *, profile, timeout=None):
        return json.dumps(
            {
                "schema_version": "siq_ic_report_factcheck_v1",
                "status": "pass",
            }
        )

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)

    with pytest.raises(ValueError, match="siq_ic_report_factcheck_v1.*contract invalid"):
        asyncio.run(
            ic_phase_orchestrator._run_r4_factcheck(
                package_dir,
                workflow_run=workflow_run,
                decision=decision,
                rendered_markdown="# R4 draft",
                evidence={},
                created_by={"id": 7},
                timeout=None,
            )
        )

    task = deal_store.read_json(package_dir / "decision" / "factcheck_task.json", {})
    assert task["status"] == "failed"
    assert task["report_id"] == decision["report_id"]
    assert task["report_revision"] == 3
    assert task["contract_validation"] == {
        "passed": False,
        "output_schema": "siq_ic_report_factcheck_v1",
        "error_type": "ICContractValidationError",
    }
    raw_path = package_dir / task["output_artifact_path"]
    raw_hash = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    assert task["output_artifact_hash"] == raw_hash
    assert task["output_artifact_hashes"] == {task["output_artifact_path"]: raw_hash}
    assert task["task_claim"]["status"] == "failed"
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})
    failure = next(
        event for event in audit["events"] if event["event_type"] == "ic_r4_factcheck_failed"
    )
    for field in (
        "workflow_run_id",
        "task_id",
        "report_id",
        "report_revision",
        "evidence_snapshot_hash",
        "prompt_contract_version",
        "profile_contract_version",
        "output_schema",
        "output_artifact_hashes",
        "contract_validation",
    ):
        assert failure[field] == task[field]
    assert failure["status"] == "failed"
    assert failure["failure_reason"] == "ICContractValidationError"


def test_r4_factcheck_retry_preserves_prior_attempt_lineage(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    workflow_run = ic_phase_orchestrator.ensure_workflow_run(package_dir)
    decision = {
        "report_id": "ICRPT-FACTCHECK-RETRY-001",
        "revision": 1,
        "deal_id": DEAL_ID,
        "evidence_snapshot_hash": SNAPSHOT,
        "decision": "review",
        "claims": [],
        "background_knowledge_refs": [],
    }
    sessions: list[str] = []
    run_ids = iter(("run-r4-factcheck-invalid-1", "run-r4-factcheck-valid-2"))

    monkeypatch.setenv("SIQ_IC_FACTCHECK_REPAIR_ATTEMPTS", "0")

    async def fake_create_run(prompt, history, *, profile, session_id=None, instructions=None):
        sessions.append(session_id)
        return next(run_ids)

    async def fake_collect(run_id, *, profile, timeout=None):
        if run_id.endswith("invalid-1"):
            return '{"schema_version":"siq_ic_report_factcheck_v1","status":"pass"}'
        return json.dumps(
            {
                "schema_version": "siq_ic_report_factcheck_v1",
                "status": "pass",
                "claim_checks": [],
                "numeric_checks": [],
                "citation_checks": [],
                "contradictions": [],
                "unsupported_claims": [],
                "required_repairs": [],
            }
        )

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)

    with pytest.raises(ValueError, match="siq_ic_report_factcheck_v1.*contract invalid"):
        asyncio.run(
            ic_phase_orchestrator._run_r4_factcheck(
                package_dir,
                workflow_run=workflow_run,
                decision=decision,
                rendered_markdown="# R4 draft",
                evidence={},
                created_by=None,
                timeout=None,
            )
        )
    first_task = deal_store.read_json(package_dir / "decision" / "factcheck_task.json", {})
    first_created_at = first_task["created_at"]
    first_raw_path = first_task["output_artifact_path"]
    first_raw_hash = first_task["output_artifact_hash"]

    factcheck, task = asyncio.run(
        ic_phase_orchestrator._run_r4_factcheck(
            package_dir,
            workflow_run=workflow_run,
            decision=decision,
            rendered_markdown="# R4 draft",
            evidence={},
            created_by=None,
            timeout=None,
        )
    )

    assert factcheck["status"] == "pass"
    assert sessions == [
        f"{workflow_run['workflow_run_id']}-{task['task_id']}-attempt-1",
        f"{workflow_run['workflow_run_id']}-{task['task_id']}-attempt-2",
    ]
    assert task["created_at"] == first_created_at
    assert task["task_claim"]["attempt"] == 2
    assert len(task["attempt_history"]) == 1
    prior = task["attempt_history"][0]
    assert prior["lease_attempt"] == 1
    assert prior["terminal_status"] == "failed"
    assert prior["hermes_run_ids"] == ["run-r4-factcheck-invalid-1"]
    assert prior["output_artifact_path"] == first_raw_path
    assert prior["output_artifact_hash"] == first_raw_hash
    assert hashlib.sha256((package_dir / first_raw_path).read_bytes()).hexdigest() == first_raw_hash


def test_r4_factcheck_allows_one_audited_contract_repair(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    workflow_run = ic_phase_orchestrator.ensure_workflow_run(package_dir)
    decision = {
        "report_id": "ICRPT-FACTCHECK-REPAIR-001",
        "revision": 1,
        "deal_id": DEAL_ID,
        "evidence_snapshot_hash": SNAPSHOT,
        "decision": "review",
        "claims": [],
        "background_knowledge_refs": [],
    }
    create_calls: list[tuple[str, str]] = []

    async def fake_create_run(prompt, history, *, profile, session_id=None, instructions=None):
        create_calls.append((prompt, session_id))
        return f"run-r4-factcheck-repair-{len(create_calls)}"

    authored = {
        "schema_version": "siq_ic_report_factcheck_v1",
        "status": "warn",
        "claim_checks": [],
        "numeric_checks": [],
        "citation_checks": [],
        "contradictions": [],
        "unsupported_claims": [],
        "required_repairs": [],
    }
    invalid_with_server_fields = {
        **authored,
        "report_id": decision["report_id"],
        "report_revision": decision["revision"],
        "checked_at": "2026-07-14T20:30:00+08:00",
        "evidence_snapshot_hash": SNAPSHOT,
    }

    async def fake_collect(run_id, *, profile, timeout=None):
        payload = invalid_with_server_fields if run_id.endswith("-1") else authored
        return json.dumps(payload)

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)

    factcheck, task = asyncio.run(
        ic_phase_orchestrator._run_r4_factcheck(
            package_dir,
            workflow_run=workflow_run,
            decision=decision,
            rendered_markdown="# R4 draft",
            evidence={},
            created_by=None,
            timeout=None,
        )
    )

    assert factcheck["status"] == "warn"
    assert len(create_calls) == 2
    assert create_calls[1][1].endswith("-attempt-1-repair-1")
    assert "不得重新核查、改变 status" in create_calls[1][0]
    assert task["hermes_run_ids"] == [
        "run-r4-factcheck-repair-1",
        "run-r4-factcheck-repair-2",
    ]
    assert task["model_execution_audit"]["attempt_count"] == 2
    assert [
        item["purpose"] for item in task["model_execution_audit"]["attempts"]
    ] == ["generation", "contract_repair"]
    assert len(task["output_artifact_paths"]) == 2
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})
    repair_event = next(
        event
        for event in audit["events"]
        if event["event_type"] == "ic_r4_factcheck_contract_repair_attempted"
    )
    assert repair_event["original_hermes_run_id"] == "run-r4-factcheck-repair-1"
    assert repair_event["repair_hermes_run_id"] == "run-r4-factcheck-repair-2"


def test_r4_factcheck_repair_rejects_status_upgrade_and_finding_deletion(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    workflow_run = ic_phase_orchestrator.ensure_workflow_run(package_dir)
    decision = {
        "report_id": "ICRPT-FACTCHECK-NONESCALATION-001",
        "revision": 1,
        "deal_id": DEAL_ID,
        "evidence_snapshot_hash": SNAPSHOT,
        "decision": "review",
        "claims": [],
        "background_knowledge_refs": [],
    }
    create_calls = 0
    original = {
        "schema_version": "siq_ic_report_factcheck_v1",
        "status": "fail",
        "claim_checks": [],
        "numeric_checks": [],
        "citation_checks": [],
        "contradictions": [],
        "unsupported_claims": [],
        "required_repairs": [
            {"id": "REPAIR-001", "message": "Remove the unsupported claim."}
        ],
        "report_id": decision["report_id"],
    }
    escalated = {
        "schema_version": "siq_ic_report_factcheck_v1",
        "status": "pass",
        "claim_checks": [],
        "numeric_checks": [],
        "citation_checks": [],
        "contradictions": [],
        "unsupported_claims": [],
        "required_repairs": [],
    }

    async def fake_create_run(prompt, history, *, profile, session_id=None, instructions=None):
        nonlocal create_calls
        create_calls += 1
        return f"run-factcheck-nonescalation-{create_calls}"

    async def fake_collect(run_id, *, profile, timeout=None):
        payload = original if run_id.endswith("-1") else escalated
        return json.dumps(payload)

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)

    with pytest.raises(ValueError, match="contract_repair_non_escalation"):
        asyncio.run(
            ic_phase_orchestrator._run_r4_factcheck(
                package_dir,
                workflow_run=workflow_run,
                decision=decision,
                rendered_markdown="# R4 draft",
                evidence={},
                created_by=None,
                timeout=None,
            )
        )
    assert create_calls == 2


def test_r4_factcheck_snapshot_change_retains_stale_terminal_state(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    workflow_run = ic_phase_orchestrator.ensure_workflow_run(package_dir)
    decision = {
        "report_id": "ICRPT-FACTCHECK-STALE-001",
        "revision": 1,
        "deal_id": DEAL_ID,
        "evidence_snapshot_hash": SNAPSHOT,
        "decision": "review",
        "claims": [],
        "background_knowledge_refs": [],
    }

    async def fake_create_run(prompt, history, *, profile, session_id=None, instructions=None):
        return "run-r4-factcheck-stale"

    async def fake_collect(run_id, *, profile, timeout=None):
        _write_json(
            package_dir / "evidence" / "evidence_snapshot.json",
            {"snapshot_hash": "b" * 64, "source_ids": ["PM:SOURCE-002"]},
        )
        return json.dumps(
            {
                "schema_version": "siq_ic_report_factcheck_v1",
                "status": "pass",
                "claim_checks": [],
                "numeric_checks": [],
                "citation_checks": [],
                "contradictions": [],
                "unsupported_claims": [],
                "required_repairs": [],
            }
        )

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)

    with pytest.raises(ValueError, match="R4 factcheck stale_on_completion"):
        asyncio.run(
            ic_phase_orchestrator._run_r4_factcheck(
                package_dir,
                workflow_run=workflow_run,
                decision=decision,
                rendered_markdown="# R4 draft",
                evidence={},
                created_by=None,
                timeout=None,
            )
        )

    task = deal_store.read_json(package_dir / "decision" / "factcheck_task.json", {})
    assert task["status"] == "stale_on_completion"
    assert task["stale_on_completion"] is True
    assert task["current_evidence_snapshot_hash"] == "b" * 64
    assert task["contract_validation"]["passed"] is True
    assert task["task_claim"]["status"] == "stale_on_completion"
    assert not (package_dir / "decision" / "factcheck.json").exists()
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})
    factcheck_events = [
        event for event in audit["events"] if event["event_type"].startswith("ic_r4_factcheck_")
    ]
    assert [event["event_type"] for event in factcheck_events] == ["ic_r4_factcheck_completed"]
    assert factcheck_events[0]["status"] == "stale_on_completion"


def test_r4_factcheck_failure_creates_audited_revision_and_revalidates(monkeypatch, tmp_path):
    package_dir = _package(tmp_path)
    _prepare_r4_inputs(package_dir)
    chairman = "siq_ic_chairman"
    calls: list[str] = []
    factcheck_count = 0

    async def fake_create_run(prompt, history, *, profile, session_id=None, instructions=None):
        calls.append(profile)
        return f"run-r4-repair-{len(calls)}-{profile}"

    async def fake_collect(run_id, *, profile, timeout=None):
        nonlocal factcheck_count
        if profile == chairman:
            return json.dumps(_r4_model_output(), ensure_ascii=False)
        factcheck_count += 1
        if factcheck_count == 1:
            return json.dumps(
                {
                    "schema_version": "siq_ic_report_factcheck_v1",
                    "status": "fail",
                    "claim_checks": [],
                    "numeric_checks": [],
                    "citation_checks": [],
                    "contradictions": [{"finding": "Clarify the condition wording."}],
                    "unsupported_claims": [],
                    "required_repairs": [{"repair": "Clarify the condition wording."}],
                }
            )
        return json.dumps(
            {
                "schema_version": "siq_ic_report_factcheck_v1",
                "status": "pass",
                "claim_checks": [],
                "numeric_checks": [],
                "citation_checks": [],
                "contradictions": [],
                "unsupported_claims": [],
                "required_repairs": [],
            }
        )

    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "create_run", fake_create_run)
    monkeypatch.setattr(ic_phase_orchestrator.hermes_client, "collect_run_result", fake_collect)
    result = asyncio.run(ic_phase_orchestrator.run_r4_model(package_dir))

    assert calls == [chairman, "siq_factchecker", chairman, "siq_factchecker"]
    assert result["status"] == "completed"
    assert result["decision"]["revision"] == 2
    assert result["decision"]["parent_report_id"] == result["initial_factcheck"]["report_id"]
    assert result["factcheck"]["status"] == "pass"
    assert result["repair_execution"]["accepted"] is True
    revision_files = list((package_dir / "decision" / "revisions").glob("ICRPT-*.json"))
    assert len(revision_files) == 2
    archived_factcheck_tasks = list(
        (package_dir / "decision" / "revisions").glob("factcheck-task-*.json")
    )
    assert len(archived_factcheck_tasks) == 1
    archived_factcheck_task = deal_store.read_json(archived_factcheck_tasks[0], {})
    current_factcheck_task = deal_store.read_json(
        package_dir / "decision" / "factcheck_task.json", {}
    )
    assert archived_factcheck_task["report_revision"] == 1
    assert archived_factcheck_task["validated_output"]["status"] == "fail"
    assert current_factcheck_task["report_revision"] == 2
    assert current_factcheck_task["attempt_history"] == []
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})
    assert any(event["event_type"] == "ic_r4_repair_revision_generated" for event in audit["events"])
    assert any(event["event_type"] == "ic_r4_factcheck_task_archived" for event in audit["events"])
