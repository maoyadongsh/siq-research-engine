from __future__ import annotations

from copy import deepcopy

import pytest
from services.ic_contract_validation import ICContractValidationError

from services import (
    deal_store,
    ic_phase_orchestrator,
    ic_profile_contract,
    ic_r3_debate,
    ic_report_contracts as reports,
    ic_task_contracts as tasks,
)

DEAL_ID = "DEAL-IC-V2-001"
SNAPSHOT = "a" * 64
WORKFLOW_RUN_ID = "ICRUN-0123456789ABCDEF"
EVIDENCE_ID = "EVID-DEAL-IC-V2-001-000001"


def _knowledge(agent_id: str, phase: str) -> dict:
    contract = ic_profile_contract.get_ic_profile_contract(agent_id)
    payload = {
        "schema_version": "siq_ic_knowledge_context_v1",
        "agent_id": agent_id,
        "phase": phase,
        "status": "current",
        "degraded_reasons": [],
        "receipt_id": f"receipt-{agent_id}-{phase}",
        "receipt_round_name": phase,
        "retrieval_status": "completed",
        "milvus_used": True,
        "shared_collections": [contract["shared_collection"]],
        "private_collections": [contract["private_knowledge_collection"]],
        "physical_collections": {
            contract["shared_collection"]: contract["shared_physical_collection"],
            contract["private_knowledge_collection"]: contract["private_physical_collection"],
        },
        "project_evidence_hits": [{"evidence_id": EVIDENCE_ID, "deal_id": DEAL_ID}],
        "shared_background_hits": [],
        "private_background_hits": [
            {
                "id": "KB-HIT-001",
                "collection": contract["private_knowledge_collection"],
                "title": "role method",
            }
        ],
        "rules": [
            "project_evidence_hits are the only project Evidence authority",
            "background knowledge must not be rewritten as EVID IDs",
        ],
    }
    payload["digest"] = tasks.canonical_input_digest(payload)
    return payload


def _runtime_task(agent_id: str = "siq_ic_finance_auditor", phase: str = "R1A") -> dict:
    knowledge = _knowledge(agent_id, phase)
    contract = ic_profile_contract.get_ic_profile_contract(agent_id)
    output_schema = {
        "R1A": "siq_ic_agent_report_v2",
        "R1B": "siq_ic_agent_report_v2",
        "R1.5": "siq_ic_r1_5_chairman_rulings_v2",
        "R2": "siq_ic_r2_revision_report_v2",
        "R3": "siq_ic_r3_debate_turn_v1",
        "R4": "siq_ic_r4_decision_v2",
    }[phase]
    task = {
        "schema_version": "siq_ic_agent_task_v2",
        "task_id": f"ICTASK-{phase.replace('.', '_')}-{agent_id}-0123456789ABCDEF",
        "workflow_run_id": WORKFLOW_RUN_ID,
        "deal_id": DEAL_ID,
        "phase": phase,
        "round_name": phase,
        "agent_id": agent_id,
        "evidence_snapshot_hash": SNAPSHOT,
        "research_identity": {
            "source_ids": ["PM:DEAL-IC-V2-001:DOC-001:PRUN-001"],
            "knowledge_digest": knowledge["digest"],
            "shared_collections": [contract["shared_collection"]],
            "private_collections": [contract["private_knowledge_collection"]],
            "background_hit_digest": "b" * 64,
        },
        "prompt_contract_version": "siq_ic_phase_prompt_v2",
        "profile_contract_version": "siq_ic_profile_matrix_v2",
        "input_artifacts": [
            {"artifact_id": "EVIDENCE", "artifact_type": "structured_phase_input", "sha256": "c" * 64}
        ],
        "background_knowledge_refs": [
            {
                "ref_id": "KBREF-TASK-001",
                "collection": contract["private_knowledge_collection"],
                "locator": "KB-HIT-001",
                "title": "role method",
                "usage": "background",
            }
        ],
        "methodology_refs": [],
        "startup_retrieval_gate": {
            "receipt_id": knowledge["receipt_id"],
            "allowed_to_speak": True,
            "project_evidence_ready": True,
            "private_background_ready": True,
            "shared_collection": contract["shared_collection"],
            "private_collection": contract["private_knowledge_collection"],
            "blocking_reasons": [],
        },
        "role_objectives": ["produce role-specific analysis"],
        "required_questions": ["what can falsify the thesis"],
        "hard_rules": ["background knowledge is not project Evidence"],
        "output_schema": output_schema,
        "timeout_seconds": 900,
        "created_at": "2026-07-13T10:00:00+08:00",
    }
    task["input_digest"] = "d" * 64
    return task


def _runtime_handoff() -> dict:
    body = {
        "workflow_run_id": WORKFLOW_RUN_ID,
        "deal_id": DEAL_ID,
        "phase": "R1B",
        "from_agent_id": "orchestrator",
        "to_agent_id": "siq_ic_risk_controller",
        "source_report_ids": ["ICRPT-STRATEGY-0001"],
        "claim_ids": ["CLM-STRAT-001"],
        "dispute_ids": [],
        "evidence_ids": [EVIDENCE_ID],
        "evidence_snapshot_hash": SNAPSHOT,
    }
    digest = tasks.canonical_input_digest(body)
    return {
        "schema_version": "siq_ic_agent_handoff_v1",
        "handoff_id": f"ICHANDOFF-{digest[:24].upper()}",
        **body,
        "input_digest": digest,
        "created_at": "2026-07-13T10:00:00+08:00",
    }


def _background_ref(agent_id: str, suffix: str = "001", usage: str = "background") -> dict:
    contract = ic_profile_contract.get_ic_profile_contract(agent_id)
    return {
        "ref_id": f"KBREF-METHOD-{suffix}",
        "collection": contract["private_knowledge_collection"],
        "locator": f"milvus:{contract['private_knowledge_collection']}:{suffix}",
        "title": "role-specific method",
        "usage": usage,
    }


def _claim(*, status: str = "verified", impact: str = "critical") -> dict:
    return {
        "claim_id": "CLM-PRIMARY-001",
        "topic": "revenue_quality",
        "conclusion": "Issuer revenue quality is supported by project documents.",
        "status": status,
        "evidence_ids": [EVIDENCE_ID] if status in {"verified", "derived"} else [],
        "counter_evidence_ids": [EVIDENCE_ID] if status == "contested" else [],
        "calculation_trace_ids": ["CALC-001"] if status == "derived" else [],
        "background_knowledge_ref_ids": ["KBREF-METHOD-001"],
        "methodology_ref_ids": [],
        "confidence": "high",
        "decision_impact": impact,
        "period": "2025",
        "currency": "CNY",
        "unit": "million",
    }


def _role_fields(agent_id: str) -> dict:
    fields: dict = {
        field: {"result": "completed"}
        for field in reports.ROLE_REQUIRED_FIELDS[agent_id]
    }
    if agent_id == "siq_ic_finance_auditor":
        fields["calculation_trace_ids"] = ["CALC-001"]
    if agent_id == "siq_ic_legal_scanner":
        fields["closing_conditions"] = [{"condition": "license confirmed"}]
        fields["unresolved_legal_questions"] = []
    if agent_id == "siq_ic_risk_controller":
        fields["warning_thresholds"] = [{"metric": "churn", "threshold": 0.1}]
        fields["stop_loss_thresholds"] = [{"metric": "cash", "threshold": 10}]
        fields["veto_flags"] = []
    if agent_id == "siq_ic_chairman":
        fields.update(
            {
                "disputes": [],
                "rulings": [],
                "six_dimension_scorecard": [{"dimension": f"D{i}"} for i in range(6)],
                "weighted_agent_score": 80,
                "chairman_dimension_score": 80,
                "conditions": [],
                "decision": "pass",
            }
        )
    return fields


def _expert_report(agent_id: str = "siq_ic_finance_auditor", phase: str = "R1A") -> dict:
    background = _background_ref(agent_id)
    gate_contract = ic_profile_contract.get_ic_profile_contract(agent_id)
    return {
        "schema_version": "siq_ic_expert_report_v2",
        "report_id": f"ICRPT-{agent_id.removeprefix('siq_ic_').upper().replace('_', '-')}-0001",
        "workflow_run_id": WORKFLOW_RUN_ID,
        "deal_id": DEAL_ID,
        "phase": phase,
        "agent_id": agent_id,
        "research_identity": {"deal_id": DEAL_ID, "source_ids": ["PM:SOURCE"]},
        "evidence_snapshot_hash": SNAPSHOT,
        "recommendation": "conditional_support",
        "score": 80,
        "confidence": "high",
        "claims": [_claim()],
        "scorecard": [
            {
                "dimension": "quality",
                "score": 80,
                "weight": 1,
                "rationale": "project Evidence supports the claim",
                "claim_ids": ["CLM-PRIMARY-001"],
                "evidence_ids": [EVIDENCE_ID],
                "confidence": "high",
            }
        ],
        "red_flags": [],
        "open_questions": [],
        "required_followups": [],
        "executive_summary": "Structured role conclusion.",
        "methodology": ["role-specific diligence"],
        "background_knowledge_refs": [background],
        "methodology_refs": [],
        "startup_receipt_id": f"receipt-{agent_id}-{phase}",
        "startup_retrieval_gate": {
            "receipt_id": f"receipt-{agent_id}-{phase}",
            "allowed_to_speak": True,
            "project_evidence_ready": True,
            "private_background_ready": True,
            "shared_collection": gate_contract["shared_collection"],
            "private_collection": gate_contract["private_knowledge_collection"],
            "blocking_reasons": [],
        },
        "limitations": [],
        "generation_mode": "model",
        "revision": 1,
        "parent_report_id": None,
        "created_at": "2026-07-13T10:00:00+08:00",
        **_role_fields(agent_id),
    }


def test_runtime_shaped_task_and_handoff_validate_with_digest_and_knowledge_gate():
    task = _runtime_task()
    handoff = _runtime_handoff()

    assert tasks.validate_agent_task(task)["input_digest"] == task["input_digest"]
    assert tasks.validate_agent_handoff(handoff)["input_digest"] == handoff["input_digest"]

    tampered = deepcopy(task)
    tampered["background_knowledge_refs"][0]["collection"] = "siq_ic_legal_scanner"
    with pytest.raises(ICContractValidationError, match="background_knowledge_collection"):
        tasks.validate_agent_task(tampered)


def test_high_severity_r15_resolved_ruling_requires_project_evidence():
    artifact = {
        "schema_version": "siq_ic_r1_5_dispute_v1",
        "dispute_id": "DISP-DEAL-IC-V2-001-001",
        "workflow_run_id": WORKFLOW_RUN_ID,
        "deal_id": DEAL_ID,
        "evidence_snapshot_hash": SNAPSHOT,
        "question": "Can the high-risk dispute be closed?",
        "severity": "high",
        "positions": [{"agent_id": "siq_ic_strategist"}, {"agent_id": "siq_ic_risk_controller"}],
        "evidence_ids": [],
        "counter_evidence_ids": [],
        "ruling": "synthesize",
        "rationale": "Close it without evidence.",
        "accepted_claim_ids": [],
        "rejected_claim_ids": [],
        "required_followups": [],
        "decision_impact": "critical",
        "created_at": "2026-07-13T10:00:00+08:00",
    }
    with pytest.raises(ICContractValidationError, match="high_severity_resolved_ruling_requires_evidence"):
        reports.validate_r1_5_dispute(artifact)

    artifact.update({
        "ruling": "needs_more_evidence",
        "required_followups": ["Obtain primary project Evidence."],
    })
    assert reports.validate_r1_5_dispute(artifact)["ruling"] == "needs_more_evidence"


def test_r3_planner_covers_safe_skip_and_full_high_risk_paths():
    base_reports = {
        "siq_ic_strategist": {"recommendation": "support", "score": 78, "claims": [], "veto_flags": []},
        "siq_ic_risk_controller": {"recommendation": "support", "score": 72, "claims": [], "veto_flags": []},
    }
    safe = ic_r3_debate.plan_r3_debate(
        deal_id=DEAL_ID,
        disputes=[],
        r2_reports=base_reports,
        evidence_quality={"status": "pass"},
        allow_skip=True,
        policy_allows_skip=True,
    )
    assert safe["mode"] == "skip"
    assert all(safe["skip_checks"].values())

    full = ic_r3_debate.plan_r3_debate(
        deal_id=DEAL_ID,
        disputes=[
            {
                "dispute_id": "DISP-DEAL-IC-V2-001-002",
                "topic": "Critical legal uncertainty",
                "severity": "critical",
                "resolved": False,
                "positions": [
                    {"agent_id": "siq_ic_strategist", "recommendation": "support", "score": 78},
                    {"agent_id": "siq_ic_risk_controller", "recommendation": "reject", "score": 40},
                ],
            }
        ],
        r2_reports={
            **base_reports,
            "siq_ic_risk_controller": {"recommendation": "reject", "score": 40, "claims": [], "veto_flags": []},
        },
        evidence_quality={"status": "pass"},
        allow_skip=True,
        policy_allows_skip=True,
    )
    assert full["mode"] == "full"
    assert full["blocking"] is True
    assert full["topics"][0]["red_agent_id"] == "siq_ic_risk_controller"


def test_r3_planner_forces_full_debate_for_open_critical_legal_red_flag():
    legal_flag = {
        "flag_id": "LEGAL-FLAG-001",
        "description": "The operating license ownership remains unverified.",
        "severity": "critical",
        "status": "open",
        "evidence_ids": [EVIDENCE_ID],
    }
    reports = {
        "siq_ic_strategist": {
            "recommendation": "support",
            "score": 78,
            "claims": [],
            "red_flags": [],
            "veto_flags": [],
        },
        "siq_ic_legal_scanner": {
            "recommendation": "conditional_support",
            "score": 72,
            "claims": [],
            "evidence_ids": [EVIDENCE_ID],
            "red_flags": [legal_flag],
            "veto_flags": [],
        },
    }

    plan = ic_r3_debate.plan_r3_debate(
        deal_id=DEAL_ID,
        disputes=[],
        r2_reports=reports,
        evidence_quality={"status": "pass"},
        allow_skip=True,
        policy_allows_skip=True,
    )

    assert plan["mode"] == "full"
    assert plan["blocking"] is True
    assert plan["skip_checks"]["no_unresolved_material_red_flags"] is False
    assert "no_unresolved_material_red_flags" in plan["skip_blocking_reasons"]
    assert plan["topics"] == [
        {
            "topic_id": "R3-RED-FLAG-001",
            "question": legal_flag["description"],
            "dimension": "legal_red_flag",
            "severity": "critical",
            "dispute_id": "R3-RED-FLAG-001",
            "red_agent_id": "siq_ic_legal_scanner",
            "blue_agent_id": "siq_ic_strategist",
            "claim_ids": [],
            "evidence_ids": [EVIDENCE_ID],
            "positions": [
                {
                    "agent_id": "siq_ic_legal_scanner",
                    "recommendation": "conditional_support",
                    "score": 72,
                    "claim_ids": [],
                    "evidence_ids": [EVIDENCE_ID],
                    "red_flags": [legal_flag],
                }
            ],
        }
    ]


def test_r3_planner_allows_safe_skip_after_critical_legal_red_flag_is_closed():
    reports = {
        "siq_ic_strategist": {
            "recommendation": "support",
            "score": 78,
            "claims": [],
            "red_flags": [],
            "veto_flags": [],
        },
        "siq_ic_legal_scanner": {
            "recommendation": "conditional_support",
            "score": 72,
            "claims": [],
            "red_flags": [
                {
                    "description": "The operating license ownership was verified.",
                    "severity": "critical",
                    "status": "resolved",
                }
            ],
            "veto_flags": [],
        },
    }

    plan = ic_r3_debate.plan_r3_debate(
        deal_id=DEAL_ID,
        disputes=[],
        r2_reports=reports,
        evidence_quality={"status": "pass"},
        allow_skip=True,
        policy_allows_skip=True,
    )

    assert plan["mode"] == "skip"
    assert plan["topics"] == []
    assert all(plan["skip_checks"].values())


def test_r3_planner_deduplicates_legacy_red_flags_and_applies_topic_budget():
    reports = {
        agent_id: {
            "recommendation": "conditional_support",
            "score": 70,
            "claims": [],
            "remaining_questions": [f"Remaining material question for {agent_id}"],
            "red_flags": [
                f"Open material flag {index} for {agent_id}"
                for index in range(1, flag_count + 1)
            ],
            "veto_flags": [],
            "evidence_ids": [EVIDENCE_ID],
        }
        for agent_id, flag_count in (
            ("siq_ic_strategist", 6),
            ("siq_ic_sector_expert", 4),
            ("siq_ic_finance_auditor", 4),
            ("siq_ic_legal_scanner", 0),
            ("siq_ic_risk_controller", 0),
        )
    }

    plan = ic_r3_debate.plan_r3_debate(
        deal_id=DEAL_ID,
        disputes=[],
        r2_reports=reports,
        evidence_quality={"status": "pass"},
        allow_skip=True,
        policy_allows_skip=True,
        max_topics=2,
    )

    assert plan["mode"] == "full"
    assert plan["blocking"] is True
    assert plan["candidate_topic_count"] == 5
    assert plan["selected_topic_count"] == 2
    assert plan["deferred_topic_count"] == 3
    assert plan["topic_budget"] == 2
    assert [item["topic_id"] for item in plan["topics"]] == ["R3-R2-001", "R3-R2-002"]
    assert [item["red_agent_id"] for item in plan["topics"]] == [
        "siq_ic_finance_auditor",
        "siq_ic_legal_scanner",
    ]
    assert all(not item["topic_id"].startswith("R3-RED-FLAG") for item in plan["deferred_topics"])
    assert sum(len(item["material_red_flags"]) for item in plan["deferred_topics"]) == 10
    assert plan["topic_selection_policy"] == "stable_material_topic_budget_v1"


def test_validator_accepts_artifacts_built_by_current_phase_orchestrator(tmp_path):
    deal_store.create_deal_package(
        deal_id=DEAL_ID,
        company_name="IC V2 Co",
        wiki_root=tmp_path,
    )
    package_dir = tmp_path / "deals" / DEAL_ID
    agent_id = "siq_ic_finance_auditor"
    receipt = {
        "receipt_id": "receipt-finance-r1a",
        "agent_id": agent_id,
        "round_name": "R1A",
        "evidence_snapshot_hash": SNAPSHOT,
        "gate": {"allowed_to_speak": True, "blocking_reasons": []},
        "retrieval_collections": ["siq_deal_shared", agent_id],
        "shared_collections": ["siq_deal_shared"],
        "private_collections": [agent_id],
        "private_collection": agent_id,
        "shared_collection": "siq_deal_shared",
        "background_retrieval_status": "completed",
        "milvus_used": True,
        "background_knowledge_hits": [
            {"id": "KB-HIT-ORCHESTRATOR", "collection": agent_id, "title": "finance method"}
        ],
        "project_evidence_hits": [{"evidence_id": EVIDENCE_ID, "deal_id": DEAL_ID}],
    }
    workflow_run = {
        "workflow_run_id": WORKFLOW_RUN_ID,
        "deal_id": DEAL_ID,
        "evidence_snapshot_hash": SNAPSHOT,
        "source_ids": ["PM:SOURCE"],
    }
    knowledge = ic_phase_orchestrator.build_knowledge_context(
        receipt,
        agent_id=agent_id,
        phase="R1A",
        expected_snapshot_hash=SNAPSHOT,
    )
    handoff = ic_phase_orchestrator.persist_handoff(
        package_dir,
        workflow_run=workflow_run,
        phase="R1A",
        from_agent_id="orchestrator",
        to_agent_id=agent_id,
        reports=[],
        payload={"independent_research": True},
        knowledge_context=knowledge,
    )
    task = ic_phase_orchestrator.build_task_envelope(
        package_dir,
        workflow_run=workflow_run,
        phase="R1A",
        round_name="R1A",
        agent_id=agent_id,
        receipt=receipt,
        handoff=handoff,
        role_objectives=["audit financial quality"],
        required_questions=["is revenue traceable"],
        output_schema="siq_ic_agent_report_v2",
        input_artifacts={"handoff": handoff},
        timeout_seconds=900,
    )

    assert tasks.validate_agent_handoff(handoff)["handoff_id"] == handoff["handoff_id"]

    store_path = package_dir / ic_phase_orchestrator.HANDOFF_STORE_PATH
    store = deal_store.read_json(store_path, {})
    store["payloads"][handoff["handoff_id"]]["payload"]["independent_research"] = False
    deal_store.write_json(store_path, store)
    with pytest.raises(ValueError, match="handoff_sidecar_digest_mismatch"):
        ic_phase_orchestrator.read_handoff_payload(package_dir, handoff["handoff_id"])
    assert tasks.validate_agent_task(task)["task_id"] == task["task_id"]


def test_task_rejects_missing_private_milvus_gate_and_background_evid_impersonation():
    task = _runtime_task()
    task["startup_retrieval_gate"]["allowed_to_speak"] = False
    task["startup_retrieval_gate"]["private_background_ready"] = False
    task["startup_retrieval_gate"]["blocking_reasons"] = ["milvus_not_used"]
    task["background_knowledge_refs"] = []

    with pytest.raises(ICContractValidationError) as exc:
        tasks.validate_agent_task(task)
    assert "startup_retrieval_gate_not_ready" in exc.value.errors


def test_expert_report_validates_role_fields_project_evidence_and_private_background_refs():
    report = _expert_report()
    known = {EVIDENCE_ID: {"deal_id": DEAL_ID, "source_type": "primary_market_prospectus"}}

    normalized = reports.validate_expert_report(
        report,
        expected_deal_id=DEAL_ID,
        expected_snapshot_hash=SNAPSHOT,
        known_evidence=known,
    )
    assert normalized["agent_id"] == "siq_ic_finance_auditor"
    assert normalized["background_knowledge_refs"][0]["collection"] == "siq_ic_finance_auditor"

    missing_trace = deepcopy(report)
    missing_trace["calculation_trace_ids"] = []
    with pytest.raises(ICContractValidationError, match="finance_calculation_trace_ids"):
        reports.validate_expert_report(missing_trace, known_evidence=known)


def test_verified_critical_claim_cannot_use_background_knowledge_instead_of_deal_evidence():
    claim = _claim()
    claim["evidence_ids"] = []

    with pytest.raises(ICContractValidationError):
        reports.validate_claim(claim, known_evidence_ids={EVIDENCE_ID})


def test_all_seven_profiles_map_to_versioned_output_contracts_and_matrix_capabilities():
    mapping = reports.profile_output_contracts()

    assert set(mapping) == {
        "siq_ic_master_coordinator",
        "siq_ic_chairman",
        "siq_ic_strategist",
        "siq_ic_sector_expert",
        "siq_ic_finance_auditor",
        "siq_ic_legal_scanner",
        "siq_ic_risk_controller",
    }
    for profile_id, output_contracts in mapping.items():
        contract = ic_profile_contract.get_ic_profile_contract(profile_id)
        assert output_contracts
        assert contract["phase_capabilities"]
        assert contract["output_schemas"]
        assert contract["private_knowledge_collection"].startswith("siq_ic_")


def test_all_seven_profiles_have_a_valid_role_specific_formal_output():
    role_phases = {
        "siq_ic_strategist": "R1A",
        "siq_ic_sector_expert": "R1A",
        "siq_ic_finance_auditor": "R1A",
        "siq_ic_legal_scanner": "R1A",
        "siq_ic_risk_controller": "R1B",
        "siq_ic_chairman": "R1B",
    }
    validated_agents = set()
    for agent_id, phase in role_phases.items():
        report = _expert_report(agent_id, phase=phase)
        normalized = reports.validate_expert_report(report, known_evidence={EVIDENCE_ID})
        validated_agents.add(normalized["agent_id"])

    coordinator = {
        "schema_version": "siq_ic_r0_readiness_v1",
        "workflow_run_id": WORKFLOW_RUN_ID,
        "deal_id": DEAL_ID,
        "agent_id": "siq_ic_master_coordinator",
        "research_identity": {"deal_id": DEAL_ID},
        "evidence_snapshot_hash": SNAPSHOT,
        "readiness": "ready",
        "material_completeness": {"prospectus": "ready"},
        "evidence_gaps": [],
        "due_diligence_plan": ["run independent expert review"],
        "task_assignments": [{"agent_id": "siq_ic_strategist", "phase": "R1A"}],
        "blocking_reasons": [],
        "created_at": "2026-07-13T10:00:00+08:00",
    }
    validated = reports.validate_r0_readiness(coordinator)
    validated_agents.add(validated["agent_id"])

    expected_agents = {
        contract["profile_id"] for contract in ic_profile_contract.list_ic_profile_contracts()
    }
    assert validated_agents == expected_agents


def test_r2_delta_and_r3_skip_contracts_enforce_deterministic_invariants():
    report = _expert_report("siq_ic_strategist", phase="R2")
    revision = {
        "schema_version": "siq_ic_r2_revision_v1",
        "report": report,
        "r1_score": 72,
        "r2_score": 80,
        "score_change": 8,
        "changed_claims": ["CLM-PRIMARY-001"],
        "unchanged_claims": [],
        "accepted_rulings": ["DSP-001"],
        "challenged_rulings": [],
        "new_evidence_ids": [EVIDENCE_ID],
        "closed_questions": [],
        "remaining_questions": [],
        "revision_rationale": "New project Evidence changed the score.",
    }
    assert reports.validate_r2_revision(revision, known_evidence={EVIDENCE_ID})["score_change"] == 8

    unsafe_skip = {
        "schema_version": "siq_ic_r3_plan_v1",
        "mode": "skip",
        "reason_codes": ["no_dispute"],
        "topics": [],
        "estimated_rounds": 0,
        "requires_human_confirmation_to_skip": True,
        "skip_checks": {"critical_disputes_closed": False},
        "human_skip_confirmation": False,
    }
    with pytest.raises(ICContractValidationError, match="skip_safety"):
        reports.validate_r3_plan(unsafe_skip)
