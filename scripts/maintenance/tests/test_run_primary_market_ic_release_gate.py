from __future__ import annotations

import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "run_primary_market_ic_release_gate.py"
    spec = importlib.util.spec_from_file_location("primary_market_ic_release_gate_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _profiles(module) -> dict[str, str]:
    matrix = json.loads(module.DEFAULT_PROFILE_MATRIX.read_text(encoding="utf-8"))
    return {profile["id"]: profile["retrieval"]["private_collection"] for profile in matrix["profiles"]}


SNAPSHOT = "a" * 64
DEAL_ID = "DEAL-GATE-001"
WORKFLOW_RUN_ID = "ICRUN-GATE-00000001"
CREATED_AT = "2026-07-13T09:00:00Z"


def _research_identity() -> dict:
    return {
        "research_id": "RESEARCH-GATE-001",
        "project_name": "Release gate contract fixture",
    }


def _background_ref(profile_id: str, *, usage: str) -> dict:
    suffix = profile_id.upper().replace("_", "-")
    return {
        "ref_id": f"KBREF-{suffix}-{usage.upper()}",
        "collection": profile_id,
        "locator": f"{usage}:{profile_id}",
        "title": f"{profile_id} {usage}",
        "usage": usage,
    }


def _startup_gate(profile_id: str) -> dict:
    return {
        "receipt_id": f"startup-{profile_id}-release-001",
        "allowed_to_speak": True,
        "project_evidence_ready": True,
        "private_background_ready": True,
        "shared_collection": "siq_deal_shared",
        "private_collection": profile_id,
        "blocking_reasons": [],
    }


def _scorecard_item(evidence_id: str, claim_id: str, *, dimension: str = "investment_case") -> dict:
    return {
        "dimension": dimension,
        "score": 82,
        "weight": 1,
        "rationale": "项目证据支持该维度判断。",
        "claim_ids": [claim_id],
        "evidence_ids": [evidence_id],
        "confidence": "high",
    }


def _six_dimension_scorecard(evidence_id: str, claim_id: str) -> list[dict]:
    return [
        _scorecard_item(evidence_id, claim_id, dimension=dimension)
        for dimension in (
            "strategy",
            "sector",
            "finance",
            "legal",
            "risk",
            "governance",
        )
    ]


def _role_fields(profile_id: str, evidence_id: str, claim_id: str) -> dict:
    if profile_id == "siq_ic_strategist":
        return {
            "policy_assessment": "政策环境对项目中性偏正面。",
            "cycle_position": "行业处于扩张中期。",
            "capital_flow_signals": "一级市场资金仍偏向头部项目。",
            "strategic_fit": "项目与组合策略匹配。",
            "scenario_matrix": [{"scenario": "base", "probability": 0.6}],
            "exit_window": "预计三至五年退出窗口。",
        }
    if profile_id == "siq_ic_sector_expert":
        return {
            "market_sizing": "可服务市场规模已由项目材料核验。",
            "competitor_matrix": [{"competitor": "Peer A", "position": "comparable"}],
            "technology_routes": ["当前技术路线具备商业化可行性。"],
            "value_chain": "项目位于价值链核心环节。",
            "market_share_evidence": [evidence_id],
            "industry_lifecycle": "growth",
        }
    if profile_id == "siq_ic_finance_auditor":
        return {
            "historical_financials": {"revenue_2025": 128000000},
            "financial_reconciliations": ["收入与现金流勾稽一致。"],
            "quality_of_earnings": "经常性经营收入占比稳定。",
            "cash_flow_assessment": "经营现金流能够覆盖基础运营。",
            "forecast_scenarios": [{"scenario": "base", "revenue": 150000000}],
            "valuation_scenarios": [{"scenario": "base", "multiple": 8}],
            "sensitivity_analysis": [{"variable": "revenue", "change": -0.1}],
            "calculation_trace_ids": ["CALC-GATE-001"],
        }
    if profile_id == "siq_ic_legal_scanner":
        return {
            "legal_issues": ["核心资质续期需列为持续监控事项。"],
            "legal_basis": ["项目材料中的许可证及公司登记文件。"],
            "severity": "medium",
            "remediation": ["交割前复核资质有效期。"],
            "closing_conditions": ["取得最新核心资质证明。"],
            "term_sheet_protections": ["设置资质失效赔偿条款。"],
            "unresolved_legal_questions": [],
        }
    if profile_id == "siq_ic_risk_controller":
        return {
            "risk_register": [{"risk": "客户集中", "severity": "high"}],
            "counter_theses": ["核心客户流失可能显著影响收入。"],
            "stress_scenarios": [{"scenario": "top_customer_loss", "impact": -0.2}],
            "risk_transmission": "客户流失通过收入和现金流传导。",
            "leading_indicators": ["核心客户续约率"],
            "warning_thresholds": [{"metric": "renewal_rate", "value": 0.8}],
            "stop_loss_thresholds": [{"metric": "renewal_rate", "value": 0.6}],
            "veto_flags": [],
        }
    if profile_id == "siq_ic_chairman":
        return {
            "consensus": ["在交割条件约束下有条件支持。"],
            "disputes": [],
            "rulings": [],
            "six_dimension_scorecard": _six_dimension_scorecard(evidence_id, claim_id),
            "weighted_agent_score": 82,
            "chairman_dimension_score": 82,
            "chairman_qualitative_decision": "关键风险可通过交割条件缓释。",
            "conditions": ["完成核心客户函证。"],
            "monitoring_metrics": ["核心客户续约率"],
            "decision": "pass",
        }
    raise AssertionError(f"unsupported fixture profile: {profile_id}")


def _expert_report(
    profile_id: str,
    phase: str,
    report_id: str,
    evidence_id: str,
    *,
    revision: int,
    parent_report_id: str | None,
) -> dict:
    claim_id = f"CLM-{phase.replace('.', '')}-{profile_id.removeprefix('siq_ic_').upper().replace('_', '-')}-001"
    report = {
        "schema_version": "siq_ic_expert_report_v2",
        "report_id": report_id,
        "workflow_run_id": WORKFLOW_RUN_ID,
        "deal_id": DEAL_ID,
        "phase": phase,
        "agent_id": profile_id,
        "research_identity": _research_identity(),
        "evidence_snapshot_hash": SNAPSHOT,
        "recommendation": "conditional_support",
        "score": 82 if phase == "R2" else 80,
        "confidence": "high",
        "claims": [_claim(evidence_id, claim_id=claim_id)],
        "scorecard": [_scorecard_item(evidence_id, claim_id)],
        "red_flags": [],
        "open_questions": [],
        "required_followups": ["持续跟踪核心客户续约。"],
        "executive_summary": "项目核心事实已由有效项目证据核验。",
        "methodology": ["按一级市场投决方法论完成角色分析。"],
        "background_knowledge_refs": [],
        "methodology_refs": [_background_ref(profile_id, usage="methodology")],
        "startup_receipt_id": _startup_gate(profile_id)["receipt_id"],
        "startup_retrieval_gate": _startup_gate(profile_id),
        "limitations": ["本结论受核心客户续约进展影响。"],
        "generation_mode": "model",
        "revision": revision,
        "parent_report_id": parent_report_id,
        "created_at": CREATED_AT,
        **_role_fields(profile_id, evidence_id, claim_id),
    }
    if phase == "R2":
        report.update(
            {
                "revision_contract_schema_version": "siq_ic_r2_revision_v1",
                "r1_score": 80,
                "r2_score": 82,
                "score_change": 2,
                "changed_claims": [claim_id],
                "unchanged_claims": [],
                "accepted_rulings": ["DISP-DEAL-GATE-001"],
                "challenged_rulings": [],
                "new_evidence_ids": [evidence_id],
                "closed_questions": ["客户集中风险已形成交割条件。"],
                "remaining_questions": [],
                "revision_rationale": "吸收 R1.5 主席裁决并补充交割条件。",
            }
        )
    return report


def _retrieval_row(profile_id: str, private_collection: str, evidence_id: str) -> dict:
    return {
        "schema_version": "siq_ic_startup_receipt_v2",
        "receipt_id": f"startup-{profile_id}-release-001",
        "deal_id": "DEAL-GATE-001",
        "agent_id": profile_id,
        "round_name": "R4"
        if profile_id == "siq_ic_chairman"
        else "R0"
        if profile_id == "siq_ic_master_coordinator"
        else "R3",
        "retrieval_status": "ready",
        "readiness_status": "current",
        "evidence_snapshot_hash": SNAPSHOT,
        "shared_collection": "siq_deal_shared",
        "private_collection": profile_id,
        "physical_collections": {
            "siq_deal_shared": "ic_collaboration_shared",
            profile_id: private_collection,
        },
        "retrieval_collections": ["siq_deal_shared", profile_id],
        "milvus_used": True,
        "vector_retrieval": {
            "status": "completed",
            "milvus_used": True,
            "collections": ["siq_deal_shared", profile_id],
            "physical_collections": {
                "siq_deal_shared": "ic_collaboration_shared",
                profile_id: private_collection,
            },
        },
        "gate": {"allowed_to_speak": True, "blocking_reasons": []},
        "private_hits": 1,
        "project_evidence_hits": [{"evidence_id": evidence_id, "source_class": "project_evidence"}],
        "background_knowledge_hits": [
            {
                "ref_id": f"KBREF-{profile_id}",
                "collection": private_collection,
                "source_class": "background_knowledge",
            }
        ],
        "background_knowledge_refs": [
            {
                "ref_id": f"KBREF-{profile_id.upper().replace('_', '-')}",
                "collection": profile_id,
                "physical_collection": private_collection,
                "locator": f"methodology:{profile_id}",
                "title": f"{profile_id} methodology",
                "usage": "background",
                "source_class": "background_knowledge",
            }
        ],
    }


def _claim(evidence_id: str, *, claim_id: str = "CLM-REVENUE-001") -> dict:
    return {
        "claim_id": claim_id,
        "topic": "revenue quality",
        "conclusion": "报告期收入增长经招股书财务表核验，且现金流勾稽结果一致。",
        "status": "verified",
        "decision_impact": "critical",
        "confidence": "high",
        "evidence_ids": [evidence_id],
        "counter_evidence_ids": [],
        "calculation_trace_ids": [],
        "background_knowledge_ref_ids": [],
        "methodology_ref_ids": [],
        "value": 128000000,
        "period": "2025",
        "currency": "CNY",
        "unit": "yuan",
    }


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _payload_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_v3_provenance(
    bundle: Path,
    module,
    *,
    release_candidate_case_id: str | None = None,
) -> None:
    evidence_id = "EVID-DEAL-GATE-001-000001"
    r0_path = bundle / "phases/r0_readiness.json"
    r1_path = bundle / "phases/r1_reports.json"
    r2_path = bundle / "phases/r2_reports.json"
    disputes_path = bundle / "phases/r1_5_disputes.json"
    r3_path = bundle / "phases/r3_reports.json"
    r4_path = bundle / "phases/r4_decision.json"
    r0 = json.loads(r0_path.read_text(encoding="utf-8"))
    r1 = json.loads(r1_path.read_text(encoding="utf-8"))
    r2 = json.loads(r2_path.read_text(encoding="utf-8"))
    disputes = json.loads(disputes_path.read_text(encoding="utf-8"))
    r3 = json.loads(r3_path.read_text(encoding="utf-8"))
    r4 = json.loads(r4_path.read_text(encoding="utf-8"))

    dispute = disputes["disputes"][0]
    ruling = dispute["chairman_ruling"]
    ruling_output_fields = (
        "dispute_id",
        "ruling",
        "rationale",
        "required_followups",
        "evidence_ids",
        "counter_evidence_ids",
        "accepted_claim_ids",
        "rejected_claim_ids",
        "decision_impact",
    )
    ruling_output = {
        "schema_version": "siq_ic_r1_5_dispute_v1",
        "dispute_id": ruling["dispute_id"],
        "workflow_run_id": ruling["workflow_run_id"],
        "deal_id": ruling["deal_id"],
        "evidence_snapshot_hash": dispute["evidence_snapshot_hash"],
        "question": dispute["question"],
        "severity": dispute["severity"],
        "positions": deepcopy(dispute["positions"]),
        **{key: deepcopy(ruling[key]) for key in ruling_output_fields if key != "dispute_id"},
        "created_at": dispute["created_at"],
        "decision": ruling["ruling"],
        "resolved": dispute["resolved"],
    }
    ruling["submission_schema_version"] = ruling_output["schema_version"]
    ruling["source_created_at"] = ruling_output["created_at"]
    for shared_field in ("evidence_snapshot_hash", "question", "severity", "positions"):
        ruling.pop(shared_field, None)
    debate = r3["debates"][0]
    turn_output_fields = (
        "argument",
        "claim_ids",
        "evidence_ids",
        "responds_to_argument_ids",
        "unanswered_points",
    )
    turn_specs = [
        {
            "phase": "R3",
            "agent_id": turn["speaker"],
            "output_schema": "siq_ic_r3_debate_turn_v1",
            "validated_output": {key: turn[key] for key in turn_output_fields},
            "binding": {"kind": "turn", "debate_id": debate["debate_id"], "round": turn["round"]},
        }
        for turn in debate["rounds"]
    ]
    verdict = debate["chairman_verdict"]
    verdict_output = {
        "outcome": verdict["ruling"],
        "rationale": verdict["rationale"],
        "accepted_argument_ids": verdict["accepted_argument_ids"],
        "rejected_argument_ids": verdict["rejected_argument_ids"],
        "evidence_ids": [evidence_id],
        "decision_impact": verdict["decision_impact"],
        "required_followups": ["持续监控客户续约率。"],
    }
    r4_output = {
        key: deepcopy(value) for key, value in r4.items() if key not in {"human_confirmation", "hermes_called"}
    }
    task_specs = [
        {
            "phase": "R0",
            "agent_id": "siq_ic_master_coordinator",
            "output_schema": "siq_ic_r0_readiness_v1",
            "validated_output": deepcopy(r0),
            "binding": {"kind": "r0"},
        },
        *[
            {
                "phase": report["phase"],
                "agent_id": report["agent_id"],
                "output_schema": "siq_ic_expert_report_v2",
                "validated_output": deepcopy(report),
                "binding": {"kind": "r1", "report_id": report["report_id"]},
            }
            for report in r1.values()
        ],
        {
            "phase": "R1.5",
            "agent_id": "siq_ic_chairman",
            "output_schema": "siq_ic_r1_5_chairman_rulings_v2",
            "validated_output": {"rulings": [ruling_output]},
            "binding": {"kind": "r1_5", "dispute_id": ruling["dispute_id"]},
        },
        *[
            {
                "phase": "R2",
                "agent_id": report["agent_id"],
                "output_schema": "siq_ic_r2_revision_v1",
                "validated_output": deepcopy(report),
                "binding": {"kind": "r2", "report_id": report["report_id"]},
            }
            for report in r2.values()
        ],
        *turn_specs,
        {
            "phase": "R3",
            "agent_id": "siq_ic_chairman",
            "output_schema": "siq_ic_r3_debate_verdict_v1",
            "validated_output": verdict_output,
            "binding": {"kind": "verdict", "debate_id": debate["debate_id"]},
        },
        {
            "phase": "R4",
            "agent_id": "siq_ic_chairman",
            "output_schema": "siq_ic_r4_decision_v2",
            "validated_output": r4_output,
            "binding": {"kind": "r4", "report_id": r4["report_id"]},
        },
    ]

    tasks: list[dict] = []
    handoffs: list[dict] = []
    handoff_payloads: dict[str, dict] = {}
    completion_events: list[dict] = []
    r3_bindings: list[dict] = []
    for index, spec in enumerate(task_specs, start=1):
        phase = spec["phase"]
        agent_id = spec["agent_id"]
        output_schema = spec["output_schema"]
        input_digest = _digest(f"task:{index}:{phase}:{agent_id}:{output_schema}")
        knowledge = {
            "digest": _digest(f"knowledge:{phase}:{agent_id}"),
            "status": "current",
            "shared_collections": ["siq_deal_shared"],
            "private_collections": [agent_id],
        }
        sidecar_body = {
            "reports": [],
            "payload": {"task_index": index},
            "project_evidence_ids": [evidence_id],
            "source_ids": ["SRC-001"],
            "background_knowledge": knowledge,
        }
        sidecar_digest = _payload_digest(sidecar_body)
        handoff_body = {
            "workflow_run_id": WORKFLOW_RUN_ID,
            "deal_id": DEAL_ID,
            "phase": phase,
            "from_agent_id": "siq_system_orchestrator",
            "to_agent_id": agent_id,
            "source_report_ids": [],
            "claim_ids": [],
            "dispute_ids": [],
            "project_evidence_ids": [evidence_id],
            "source_ids": ["SRC-001"],
            "reports": [],
            "payload": {"task_index": index},
            "background_knowledge": knowledge,
            "sidecar_digest": sidecar_digest,
            "evidence_snapshot_hash": SNAPSHOT,
        }
        handoff_digest = _payload_digest(handoff_body)
        task_id = f"ICTASK-{input_digest[:24].upper()}"
        handoff_id = f"ICHANDOFF-{handoff_digest[:24].upper()}"
        hermes_run_id = f"run_gate_{index:03d}"
        raw_path = f"audit/ic_agent_outputs/{task_id}/{hermes_run_id}.txt"
        raw_file = bundle / raw_path
        raw_file.parent.mkdir(parents=True, exist_ok=True)
        raw_file.write_text(json.dumps(spec["validated_output"], ensure_ascii=False) + "\n", encoding="utf-8")
        artifact_schema = None if phase == "R1.5" else "siq_ic_expert_report_v2" if phase == "R2" else output_schema
        contract_validation = {
            "passed": True,
            "output_schema": output_schema,
            "artifact_schema": artifact_schema,
            "validated_by": "ic_phase_orchestrator",
        }
        runtime = {
            "schema_version": "hermes.run_runtime.v1",
            "requested_model": agent_id,
            "configured": {"provider": "fixture-provider", "model": "fixture-model-v1"},
            "effective": {"provider": "fixture-provider", "model": "fixture-model-v1"},
            "fallback": {"activated": False},
        }
        prompt_sha256 = _digest(f"prompt:{task_id}:{hermes_run_id}")
        model_execution_audit = {
            "schema_version": "siq_ic_model_execution_audit_v1",
            "runtime_metadata_status": "verified",
            "attempt_count": 1,
            "attempts": [
                {
                    "hermes_run_id": hermes_run_id,
                    "purpose": "generation",
                    "prompt_sha256": prompt_sha256,
                    "terminal_status": "succeeded",
                    "runtime_metadata_status": "verified",
                    "runtime": runtime,
                }
            ],
            "final_hermes_run_id": hermes_run_id,
            "final_prompt_sha256": prompt_sha256,
            "final_runtime": runtime,
        }
        task = {
            "schema_version": "siq_ic_agent_task_v2",
            "task_id": task_id,
            "workflow_run_id": WORKFLOW_RUN_ID,
            "deal_id": DEAL_ID,
            "phase": phase,
            "round_name": "R1" if phase in {"R1A", "R1B"} else phase,
            "agent_id": agent_id,
            "research_identity": _research_identity(),
            "evidence_snapshot_hash": SNAPSHOT,
            "prompt_contract_version": "siq_ic_phase_prompt_v5",
            "profile_contract_version": "hermes_profile_authority_v1",
            "input_artifacts": [
                {
                    "artifact_id": "evidence-snapshot",
                    "artifact_type": "evidence_snapshot",
                    "sha256": SNAPSHOT,
                }
            ],
            "background_knowledge_refs": [],
            "methodology_refs": [_background_ref(agent_id, usage="methodology")],
            "startup_retrieval_gate": _startup_gate(agent_id),
            "input_digest": input_digest,
            "role_objectives": ["完成当前阶段角色职责。"],
            "required_questions": ["关键结论是否有项目证据支持？"],
            "hard_rules": ["不得把背景知识作为项目事实证据。"],
            "output_schema": output_schema,
            "timeout_seconds": 600,
            "created_at": CREATED_AT,
            "status": "succeeded",
            "generation_mode": "hermes_model",
            "hermes_called": True,
            "task_claim": {
                "status": "succeeded",
                "attempt": 1,
                "owner": f"fixture-owner-{index}",
            },
            "attempt_history": [],
            "hermes_run_id": hermes_run_id,
            "hermes_run_ids": [hermes_run_id],
            "output_artifact_paths": [raw_path],
            "output_artifact_hashes": {raw_path: _sha256(raw_file)},
            "handoff_id": handoff_id,
            "handoff_digest": handoff_digest,
            "contract_validation": contract_validation,
            "model_execution_audit": model_execution_audit,
            "validated_output": spec["validated_output"],
            "completed_at": "2026-07-13T09:30:00Z",
        }
        tasks.append(task)
        handoffs.append(
            {
                "schema_version": "siq_ic_agent_handoff_v2",
                "handoff_id": handoff_id,
                **handoff_body,
                "input_digest": handoff_digest,
                "created_at": CREATED_AT,
            }
        )
        handoff_payloads[handoff_id] = {
            "handoff_id": handoff_id,
            **sidecar_body,
            "content_digest": sidecar_digest,
        }
        completion_events.append(
            {
                "event_type": "ic_phase_hermes_task_completed",
                "workflow_run_id": WORKFLOW_RUN_ID,
                "task_id": task_id,
                "phase": phase,
                "agent_id": agent_id,
                "input_digest": input_digest,
                "handoff_digest": handoff_digest,
                "hermes_run_id": hermes_run_id,
                "evidence_snapshot_hash": SNAPSHOT,
                "prompt_contract_version": "siq_ic_phase_prompt_v5",
                "profile_contract_version": "hermes_profile_authority_v1",
                "output_schema": output_schema,
                "output_artifact_hashes": task["output_artifact_hashes"],
                "contract_validation": contract_validation,
                "model_execution_audit": model_execution_audit,
                "status": "succeeded",
                "created_at": "2026-07-13T09:30:00Z",
            }
        )

        binding = spec["binding"]
        metadata = {
            "task_id": task_id,
            "workflow_run_id": WORKFLOW_RUN_ID,
            "input_digest": input_digest,
            "handoff_digest": handoff_digest,
            "hermes_run_id": hermes_run_id,
        }
        if binding["kind"] == "r0":
            r0.update(metadata)
        elif binding["kind"] in {"r1", "r2"}:
            reports = r1 if binding["kind"] == "r1" else r2
            report = next(item for item in reports.values() if item["report_id"] == binding["report_id"])
            report.update(metadata)
        elif binding["kind"] == "r1_5":
            ruling.update(metadata)
        elif binding["kind"] in {"turn", "verdict"}:
            r3_bindings.append(
                {
                    **binding,
                    **metadata,
                    "agent_id": agent_id,
                    "validated_output": deepcopy(spec["validated_output"]),
                }
            )
        elif binding["kind"] == "r4":
            r4.update(metadata)

    r3["task_bindings"] = r3_bindings
    debate = r3["debates"][0]
    turn_types = {
        1: "red_thesis",
        2: "blue_defense",
        3: "red_rebuttal",
        4: "blue_final_response",
    }
    arguments = []
    verdict_artifact = None
    for binding in r3_bindings:
        if binding["kind"] == "turn":
            persisted_round = next(item for item in debate["rounds"] if item["round"] == binding["round"])
            arguments.append(
                {
                    **deepcopy(binding["validated_output"]),
                    "schema_version": "siq_ic_r3_debate_turn_v1",
                    "agent_id": binding["agent_id"],
                    "turn_type": turn_types[binding["round"]],
                    "argument_id": persisted_round["argument_id"],
                    **{
                        key: binding[key]
                        for key in (
                            "task_id",
                            "workflow_run_id",
                            "input_digest",
                            "handoff_digest",
                            "hermes_run_id",
                        )
                    },
                }
            )
        else:
            verdict_artifact = {
                **deepcopy(binding["validated_output"]),
                "schema_version": "siq_ic_r3_debate_verdict_v1",
                "topic_id": "DISP-DEAL-GATE-001",
                "resolved": True,
                **{
                    key: binding[key]
                    for key in (
                        "task_id",
                        "workflow_run_id",
                        "input_digest",
                        "handoff_digest",
                        "hermes_run_id",
                    )
                },
            }
    r3["topics"] = [
        {
            "topic_id": "DISP-DEAL-GATE-001",
            "question": "客户集中风险是否已被可执行条件充分缓释？",
            "severity": "high",
            "red_agent_id": "siq_ic_risk_controller",
            "blue_agent_id": "siq_ic_strategist",
            "arguments": arguments,
            "verdict": verdict_artifact,
            "debate_contract": deepcopy(debate),
        }
    ]
    _write_json(r0_path, r0)
    _write_json(r1_path, r1)
    _write_json(r2_path, r2)
    _write_json(disputes_path, disputes)
    _write_json(r3_path, r3)
    _write_json(
        bundle / "phases/ic_agent_tasks.json",
        {"schema_version": "siq_ic_agent_tasks_v1", "tasks": tasks},
    )
    _write_json(
        bundle / "phases/ic_agent_handoffs.json",
        {
            "schema_version": "siq_ic_agent_handoffs_v1",
            "handoffs": handoffs,
            "payloads": handoff_payloads,
        },
    )
    _write_json(
        bundle / "phases/ic_workflow_runs.json",
        {
            "schema_version": "siq_ic_workflow_runs_v1",
            "runs": [
                {
                    "schema_version": "siq_ic_workflow_run_v1",
                    "workflow_run_id": WORKFLOW_RUN_ID,
                    "deal_id": DEAL_ID,
                    "status": "completed",
                    "evidence_snapshot_hash": SNAPSHOT,
                    "source_ids": ["SRC-001"],
                    "active_sources": [{"source_id": "SRC-001"}],
                    "created_at": CREATED_AT,
                    "updated_at": "2026-07-13T10:00:00Z",
                }
            ],
        },
    )

    quality = json.loads((bundle / "decision/report_quality.json").read_text(encoding="utf-8"))
    factcheck = json.loads((bundle / "decision/factcheck.json").read_text(encoding="utf-8"))
    decision_body = dict(r4)
    decision_body.pop("human_confirmation", None)
    attestation = {
        "attestation_schema_version": "siq_ic_human_confirmation_attestation_v1",
        "report_id": r4["report_id"],
        "report_revision": r4["revision"],
        "workflow_run_id": WORKFLOW_RUN_ID,
        "evidence_snapshot_hash": SNAPSHOT,
        "decision_sha256": _payload_digest(decision_body),
        "quality_sha256": _payload_digest(quality),
        "factcheck_sha256": _payload_digest(factcheck),
    }
    r4["human_confirmation"].update({"confirmed": True, **attestation})
    _write_json(r4_path, r4)

    workflow_path = bundle / "phases/workflow_state.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    workflow["phases"] = {
        "R4": {
            "human_confirmation_status": "confirmed",
            "human_confirmation": r4["human_confirmation"],
        }
    }
    _write_json(workflow_path, workflow)

    workflow_runs_path = bundle / "phases/ic_workflow_runs.json"
    workflow_runs = json.loads(workflow_runs_path.read_text(encoding="utf-8"))
    workflow_runs["runs"][0].update(
        {
            "status": "completed",
            "completed_at": "2026-07-13T10:00:00Z",
            "completion": {
                "status": "confirmed",
                "confirmed_by": {"id": 7, "username": "methodology-owner"},
                "confirmed_at": "2026-07-13T10:00:00Z",
                **{key: value for key, value in attestation.items() if key != "attestation_schema_version"},
            },
        }
    )
    _write_json(workflow_runs_path, workflow_runs)

    fact_digest = _digest("factcheck:DEAL-GATE-001:R4")
    factcheck = json.loads((bundle / "decision/factcheck.json").read_text(encoding="utf-8"))
    factcheck_task_id = f"ICFACT-{fact_digest[:24].upper()}"
    factcheck_run_id = "run_gate_factcheck_001"
    factcheck_raw_path = f"audit/ic_agent_outputs/{factcheck_task_id}/{factcheck_run_id}.txt"
    factcheck_raw_file = bundle / factcheck_raw_path
    factcheck_raw_file.parent.mkdir(parents=True, exist_ok=True)
    factcheck_raw_file.write_text(json.dumps(factcheck, ensure_ascii=False) + "\n", encoding="utf-8")
    factcheck_contract = {
        "passed": True,
        "output_schema": "siq_ic_report_factcheck_v1",
        "artifact_schema": "siq_ic_report_factcheck_v1",
        "validated_by": "ic_phase_orchestrator",
    }
    factcheck_runtime = {
        "schema_version": "hermes.run_runtime.v1",
        "requested_model": "siq_factchecker",
        "configured": {"provider": "fixture-provider", "model": "fixture-factcheck-v1"},
        "effective": {"provider": "fixture-provider", "model": "fixture-factcheck-v1"},
        "fallback": {"activated": False},
    }
    factcheck_prompt_sha256 = _digest(f"prompt:{factcheck_task_id}:{factcheck_run_id}")
    factcheck_model_audit = {
        "schema_version": "siq_ic_model_execution_audit_v1",
        "runtime_metadata_status": "verified",
        "attempt_count": 1,
        "attempts": [
            {
                "hermes_run_id": factcheck_run_id,
                "purpose": "generation",
                "prompt_sha256": factcheck_prompt_sha256,
                "terminal_status": "succeeded",
                "runtime_metadata_status": "verified",
                "runtime": factcheck_runtime,
            }
        ],
        "final_hermes_run_id": factcheck_run_id,
        "final_prompt_sha256": factcheck_prompt_sha256,
        "final_runtime": factcheck_runtime,
    }
    factcheck_task = {
        "schema_version": "siq_ic_factcheck_task_v1",
        "task_id": factcheck_task_id,
        "workflow_run_id": WORKFLOW_RUN_ID,
        "deal_id": DEAL_ID,
        "phase": "R4",
        "agent_id": "siq_factchecker",
        "report_id": "ICRPT-R4-GATE-00000001",
        "report_revision": 1,
        "evidence_snapshot_hash": SNAPSHOT,
        "prompt_contract_version": "siq_ic_phase_prompt_v5",
        "profile_contract_version": "hermes_profile_authority_v1",
        "output_schema": "siq_ic_report_factcheck_v1",
        "input_digest": fact_digest,
        "status": "succeeded",
        "generation_mode": "hermes_model",
        "hermes_called": True,
        "task_claim": {
            "status": "succeeded",
            "attempt": 1,
            "owner": "fixture-factcheck-owner",
        },
        "attempt_history": [],
        "hermes_run_id": factcheck_run_id,
        "hermes_run_ids": [factcheck_run_id],
        "output_artifact_path": factcheck_raw_path,
        "output_artifact_paths": [factcheck_raw_path],
        "output_artifact_hash": _sha256(factcheck_raw_file),
        "output_artifact_hashes": {factcheck_raw_path: _sha256(factcheck_raw_file)},
        "contract_validation": factcheck_contract,
        "model_execution_audit": factcheck_model_audit,
        "validated_output": factcheck,
        "completed_at": "2026-07-13T09:50:00Z",
    }
    _write_json(bundle / "decision/factcheck_task.json", factcheck_task)
    completion_events.extend(
        [
            {
                "event_type": "ic_r4_factcheck_completed",
                "workflow_run_id": WORKFLOW_RUN_ID,
                "task_id": factcheck_task["task_id"],
                "phase": "R4",
                "agent_id": "siq_factchecker",
                "report_id": factcheck_task["report_id"],
                "report_revision": factcheck_task["report_revision"],
                "input_digest": fact_digest,
                "hermes_run_id": factcheck_task["hermes_run_id"],
                "evidence_snapshot_hash": SNAPSHOT,
                "prompt_contract_version": "siq_ic_phase_prompt_v5",
                "profile_contract_version": "hermes_profile_authority_v1",
                "output_schema": "siq_ic_report_factcheck_v1",
                "output_artifact_hashes": factcheck_task["output_artifact_hashes"],
                "contract_validation": factcheck_contract,
                "model_execution_audit": factcheck_model_audit,
                "status": "succeeded",
                "factcheck_status": "pass",
                "created_at": "2026-07-13T09:50:00Z",
            },
            {
                "event_type": "r4_human_confirmation_updated",
                "status": "confirmed",
                "confirmed_by": {"id": 7, "username": "methodology-owner"},
                **{key: value for key, value in attestation.items() if key != "attestation_schema_version"},
                "created_at": "2026-07-13T10:00:01Z",
            },
        ]
    )
    audit = {"events": completion_events}
    _write_json(bundle / "phases/audit_log.json", audit)
    _write_json(bundle / "audit/audit_log.json", audit)

    smoke_path = bundle / "release/real_smoke.json"
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    smoke["phase_runs"] = {
        phase: {
            "status": "passed",
            "hermes_called": True,
            "workflow_advanced": True,
        }
        for phase in ("R0", "R1", "R1.5", "R2", "R3", "R4")
    }
    smoke["profile_results"] = {
        profile_id: {
            "status": "passed",
            "tasks": [
                {
                    "task_id": task["task_id"],
                    "profile_id": task["agent_id"],
                    "phase": task["phase"],
                    "hermes_run_id": task["hermes_run_id"],
                    "input_digest": task["input_digest"],
                    "handoff_digest": task["handoff_digest"],
                    "evidence_snapshot_hash": SNAPSHOT,
                    "status": "succeeded",
                    "contract_validation": {
                        "passed": True,
                        "validated_by": "ic_phase_orchestrator",
                        "artifact_schema": task["contract_validation"]["artifact_schema"],
                    },
                    "model_execution_audit": deepcopy(task["model_execution_audit"]),
                }
                for task in tasks
                if task["agent_id"] == profile_id
            ],
        }
        for profile_id in module.REQUIRED_PROFILE_IDS
    }
    smoke["contract_validation"] = {"passed": True, "errors": []}
    _write_json(smoke_path, smoke)

    manifest = json.loads(module.DEFAULT_MANIFEST.read_text(encoding="utf-8"))
    required_by_case = {
        case["case_id"]: case["required_paths"]
        for case in manifest["cases"]
        if case["case_id"] in module.REQUIRED_INDEPENDENT_GOLDEN_CASE_IDS
    }
    bindings = []
    for index, case_id in enumerate(sorted(required_by_case), start=1):
        is_release_candidate = case_id == release_candidate_case_id
        deal_id = DEAL_ID if is_release_candidate else f"DEAL-GOLDEN-{index:03d}"
        case_bundle = bundle if is_release_candidate else bundle.parent / deal_id
        run_id = "SMOKE-20260713-001" if is_release_candidate else f"GOLDEN-RUN-{index:03d}"
        result_id = f"GOLDEN-RESULT-{index:03d}"
        snapshot_hash = SNAPSHOT if is_release_candidate else _digest(f"snapshot:{case_id}")
        source_path = case_bundle / "evaluation/source.json"
        _write_json(
            source_path,
            {
                "case_id": case_id,
                "deal_id": deal_id,
                "run_id": run_id,
                "evidence_snapshot_hash": snapshot_hash,
            },
        )
        path_results = {}
        for path_index, required_path in enumerate(required_by_case[case_id], start=1):
            artifact_path = case_bundle / "evaluation" / f"path-{path_index:02d}.json"
            _write_json(
                artifact_path,
                {
                    "schema_version": "siq_ic_golden_path_evaluation_v1",
                    "case_id": case_id,
                    "required_path": required_path,
                    "deal_id": deal_id,
                    "run_id": run_id,
                    "evidence_snapshot_hash": snapshot_hash,
                    "status": "passed",
                    "quality_accepted": False,
                    "evaluator": {
                        "name": "primary-market-ic-golden-evaluator",
                        "version": "v1",
                        "deterministic_checks": True,
                    },
                    "source_artifacts": [
                        {
                            "path": source_path.relative_to(case_bundle).as_posix(),
                            "exists": True,
                            "sha256": _sha256(source_path),
                        }
                    ],
                    "assertions": [
                        {
                            "name": required_path,
                            "expected": "passed",
                            "actual": "passed",
                            "passed": True,
                        },
                        {
                            "name": "path.source_artifacts_observed",
                            "expected": True,
                            "actual": True,
                            "passed": True,
                        },
                    ],
                },
            )
            path_results[required_path] = {
                "status": "passed",
                "artifact_path": artifact_path.relative_to(case_bundle).as_posix(),
                "artifact_sha256": _sha256(artifact_path),
            }
        result_path = case_bundle / "release/golden_case_result.json"
        _write_json(
            result_path,
            {
                "schema_version": "siq_ic_golden_case_result_v1",
                "case_id": case_id,
                "run_id": run_id,
                "result_id": result_id,
                "deal_id": deal_id,
                "status": "passed",
                "quality_accepted": False,
                "evidence_snapshot_hash": snapshot_hash,
                "evaluated_at": "2026-07-13T09:45:00Z",
                "evaluator": {
                    "name": "primary-market-ic-golden-evaluator",
                    "version": "v1",
                    "deterministic_checks": True,
                },
                "path_results": path_results,
                "errors": [],
            },
        )
        bindings.append(
            {
                "case_id": case_id,
                "run_id": run_id,
                "result_id": result_id,
                "deal_id": deal_id,
                "bundle_path": case_bundle.relative_to(bundle.parent).as_posix(),
                "result_path": result_path.relative_to(case_bundle).as_posix(),
                "result_sha256": _sha256(result_path),
            }
        )
    golden_bindings_path = bundle / "release/golden_case_bindings.json"
    _write_json(
        golden_bindings_path,
        {
            "schema_version": "siq_ic_golden_case_bindings_v1",
            "suite_id": "GOLDEN-SUITE-PMIC-20260713-001",
            "status": "passed",
            "quality_accepted": False,
            "generated_at": "2026-07-13T09:55:00Z",
            "bindings": bindings,
        },
    )
    _write_json(
        bundle / "release/human_methodology_approval.json",
        {
            "schema_version": "siq_ic_human_methodology_approval_v3",
            "deal_id": "DEAL-GATE-001",
            "status": "approved",
            "approved_by": {"id": "U-001", "name": "IC Methodology Owner"},
            "approved_at": "2026-07-13T10:10:00Z",
            "methodology_version": "PMIC-2026-07-13",
            "scope": "primary_market_ic_behavior_release",
            "golden_case_suite_id": "GOLDEN-SUITE-PMIC-20260713-001",
            "golden_case_bindings_sha256": _sha256(golden_bindings_path),
            "report_binding": {
                "report_id": "ICRPT-R4-GATE-00000001",
                "revision": 1,
                "evidence_snapshot_hash": SNAPSHOT,
            },
            "human_confirmation_binding": {
                "status": "confirmed",
                "confirmed_by": {"id": 7, "username": "methodology-owner"},
                "confirmed_at": "2026-07-13T10:00:00Z",
                "audit_event_created_at": "2026-07-13T10:00:01Z",
                **attestation,
            },
        },
    )


def _make_complete_bundle(
    tmp_path: Path,
    module,
    *,
    release_candidate_case_id: str | None = None,
) -> Path:
    bundle = tmp_path / "DEAL-GATE-001"
    evidence_id = "EVID-DEAL-GATE-001-000001"
    profile_collections = _profiles(module)
    rows = {
        profile_id: _retrieval_row(profile_id, collection, evidence_id)
        for profile_id, collection in profile_collections.items()
    }

    _write_json(
        bundle / "manifest.json",
        {
            "schema_version": "siq_deal_manifest_v1",
            "deal_id": "DEAL-GATE-001",
            "documents": [],
        },
    )
    _write_json(
        bundle / "evidence/evidence_index.json",
        {
            "schema_version": "siq_deal_evidence_index_v1",
            "deal_id": "DEAL-GATE-001",
            "items": [{"evidence_id": evidence_id}],
        },
    )
    _write_json(
        bundle / "evidence/evidence_snapshot.json",
        {
            "schema_version": "siq_deal_evidence_snapshot_v1",
            "deal_id": "DEAL-GATE-001",
            "snapshot_hash": SNAPSHOT,
            "active_sources": [{"source_id": "SRC-001"}],
            "source_ids": ["SRC-001"],
            "created_at": "2026-07-13T08:30:00Z",
        },
    )
    (bundle / "evidence/evidence_items.ndjson").write_text(
        json.dumps(
            {
                "evidence_id": evidence_id,
                "source_class": "project_evidence",
                "source_id": "SRC-001",
                "page": 88,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_json(
        bundle / "phases/workflow_state.json",
        {
            "schema_version": "siq_deal_workflow_state_v1",
            "deal_id": "DEAL-GATE-001",
            "current_phase": "R4",
        },
    )
    _write_json(
        bundle / "phases/r0_readiness.json",
        {
            "schema_version": "siq_ic_r0_readiness_v1",
            "workflow_run_id": WORKFLOW_RUN_ID,
            "deal_id": DEAL_ID,
            "agent_id": "siq_ic_master_coordinator",
            "research_identity": _research_identity(),
            "evidence_snapshot_hash": SNAPSHOT,
            "readiness": "ready",
            "material_completeness": {"prospectus": "complete"},
            "evidence_gaps": [],
            "due_diligence_plan": ["完成七角色独立研究与交叉复核。"],
            "task_assignments": [{"agent_id": "siq_ic_strategist", "phase": "R1A"}],
            "blocking_reasons": [],
            "created_at": CREATED_AT,
        },
    )
    _write_json(
        bundle / "phases/startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v2",
            "deal_id": "DEAL-GATE-001",
            "agents": rows,
            "by_agent_phase": {profile_id: {receipt["round_name"]: receipt} for profile_id, receipt in rows.items()},
        },
    )

    r1_reports = {}
    r2_reports = {}
    r1_agent_ids = sorted(set(profile_collections) - {"siq_ic_master_coordinator"})
    r2_agent_ids = sorted(set(r1_agent_ids) - {"siq_ic_chairman"})
    for index, profile_id in enumerate(r1_agent_ids, start=1):
        phase = "R1B" if profile_id in {"siq_ic_chairman", "siq_ic_risk_controller"} else "R1A"
        r1_reports[profile_id] = _expert_report(
            profile_id,
            phase,
            f"ICRPT-R1-{index:08d}",
            evidence_id,
            revision=1,
            parent_report_id=None,
        )
    for index, profile_id in enumerate(r2_agent_ids, start=1):
        r2_reports[profile_id] = _expert_report(
            profile_id,
            "R2",
            f"ICRPT-R2-{index:08d}",
            evidence_id,
            revision=2,
            parent_report_id=r1_reports[profile_id]["report_id"],
        )
    _write_json(bundle / "phases/r1_reports.json", r1_reports)
    _write_json(bundle / "phases/r2_reports.json", r2_reports)
    _write_json(
        bundle / "phases/r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": "DEAL-GATE-001",
            "disputes": [
                {
                    "dispute_id": "DISP-DEAL-GATE-001",
                    "topic": "客户集中风险是否可由交割条件缓释？",
                    "question": "客户集中风险是否可由交割条件缓释？",
                    "severity": "high",
                    "positions": [
                        {"agent_id": "siq_ic_strategist", "recommendation": "support"},
                        {"agent_id": "siq_ic_risk_controller", "recommendation": "reject"},
                    ],
                    "status": "resolved",
                    "resolved": True,
                    "workflow_run_id": WORKFLOW_RUN_ID,
                    "deal_id": DEAL_ID,
                    "evidence_snapshot_hash": SNAPSHOT,
                    "evidence_ids": [evidence_id],
                    "counter_evidence_ids": [],
                    "created_at": CREATED_AT,
                    "chairman_ruling": {
                        "schema_version": "siq_deal_r1_5_dispute_ruling_v1",
                        "deal_id": DEAL_ID,
                        "dispute_id": "DISP-DEAL-GATE-001",
                        "agent_id": "siq_ic_chairman",
                        "workflow_run_id": WORKFLOW_RUN_ID,
                        "evidence_snapshot_hash": SNAPSHOT,
                        "question": "客户集中风险是否可由交割条件缓释？",
                        "severity": "high",
                        "positions": [
                            {"agent_id": "siq_ic_strategist", "recommendation": "support"},
                            {"agent_id": "siq_ic_risk_controller", "recommendation": "reject"},
                        ],
                        "ruling": "synthesize",
                        "decision": "synthesize",
                        "resolved": True,
                        "rationale": "以客户集中度阈值和交割前函证作为有条件支持前提。",
                        "required_followups": ["完成核心客户函证。"],
                        "evidence_ids": [evidence_id],
                        "counter_evidence_ids": [],
                        "accepted_claim_ids": [],
                        "rejected_claim_ids": [],
                        "decision_impact": "material",
                        "generation_mode": "model",
                        "created_at": CREATED_AT,
                    },
                }
            ],
            "generation_mode": "deterministic_r1_report_scan_v1",
        },
    )
    _write_json(
        bundle / "phases/r3_reports.json",
        {
            "schema_version": "siq_ic_r3_debate_bundle_v2",
            "deal_id": "DEAL-GATE-001",
            "evidence_snapshot_hash": SNAPSHOT,
            "mode": "full",
            "generation_mode": "hermes_dynamic_debate_v1",
            "hermes_called": True,
            "stages": ["red", "blue", "rebuttal", "verdict"],
            "plan": {
                "schema_version": "siq_ic_r3_plan_v1",
                "mode": "full",
                "reason_codes": ["high_severity_dispute"],
                "topics": [{"topic_id": "DISP-DEAL-GATE-001"}],
                "estimated_rounds": 4,
                "requires_human_confirmation_to_skip": False,
                "skip_checks": {"high_severity_disputes_closed": False},
                "human_skip_confirmation": False,
            },
            "debates": [
                {
                    "schema_version": "siq_ic_r3_debate_v1",
                    "debate_id": "DEB-GATE-000001",
                    "workflow_run_id": WORKFLOW_RUN_ID,
                    "deal_id": DEAL_ID,
                    "evidence_snapshot_hash": SNAPSHOT,
                    "topic": "客户集中风险是否已被可执行条件充分缓释？",
                    "red_team": ["siq_ic_risk_controller"],
                    "blue_team": ["siq_ic_strategist"],
                    "rounds": [
                        {
                            "argument_id": "ARG-GATE-RED-001",
                            "round": 1,
                            "speaker": "siq_ic_risk_controller",
                            "argument": "收入集中度可能放大下行情景。",
                            "claim_ids": ["CLM-R3-RISK-001"],
                            "evidence_ids": [evidence_id],
                            "responds_to_argument_ids": [],
                            "unanswered_points": ["需确认客户续约阈值。"],
                        },
                        {
                            "argument_id": "ARG-GATE-BLUE-001",
                            "round": 2,
                            "speaker": "siq_ic_strategist",
                            "argument": "续约和回款证据支持基础情景。",
                            "claim_ids": ["CLM-R3-STRATEGY-001"],
                            "evidence_ids": [evidence_id],
                            "responds_to_argument_ids": ["ARG-GATE-RED-001"],
                            "unanswered_points": ["需将阈值写入交割条件。"],
                        },
                        {
                            "argument_id": "ARG-GATE-RED-002",
                            "round": 3,
                            "speaker": "siq_ic_risk_controller",
                            "argument": "仍需设置客户流失触发阈值。",
                            "claim_ids": ["CLM-R3-RISK-002"],
                            "evidence_ids": [evidence_id],
                            "responds_to_argument_ids": ["ARG-GATE-BLUE-001"],
                            "unanswered_points": ["需明确违约救济。"],
                        },
                        {
                            "argument_id": "ARG-GATE-BLUE-002",
                            "round": 4,
                            "speaker": "siq_ic_strategist",
                            "argument": "交割条件提供可执行阈值。",
                            "claim_ids": ["CLM-R3-STRATEGY-002"],
                            "evidence_ids": [evidence_id],
                            "responds_to_argument_ids": ["ARG-GATE-RED-002"],
                            "unanswered_points": [],
                        },
                    ],
                    "chairman_verdict": {
                        "ruling": "synthesize",
                        "rationale": "以交割条件和持续监控阈值综合两方观点。",
                        "accepted_argument_ids": ["ARG-GATE-RED-002", "ARG-GATE-BLUE-002"],
                        "rejected_argument_ids": [],
                        "decision_impact": "material",
                    },
                    "status": "resolved",
                    "created_at": CREATED_AT,
                }
            ],
        },
    )
    _write_json(
        bundle / "phases/r4_decision.json",
        {
            "schema_version": "siq_ic_r4_decision_v2",
            "report_id": "ICRPT-R4-GATE-00000001",
            "revision": 1,
            "workflow_run_id": WORKFLOW_RUN_ID,
            "deal_id": DEAL_ID,
            "agent_id": "siq_ic_chairman",
            "research_identity": _research_identity(),
            "evidence_snapshot_hash": SNAPSHOT,
            "recommendation": "conditional_support",
            "generation_mode": "model",
            "hermes_called": True,
            "claims": [_claim(evidence_id, claim_id="CLM-R4-001")],
            "background_knowledge_refs": [],
            "methodology_refs": [_background_ref("siq_ic_chairman", usage="methodology")],
            "startup_receipt_id": _startup_gate("siq_ic_chairman")["receipt_id"],
            "startup_retrieval_gate": _startup_gate("siq_ic_chairman"),
            "six_dimension_scorecard": _six_dimension_scorecard(evidence_id, "CLM-R4-001"),
            "weighted_agent_score": 82,
            "chairman_dimension_score": 82,
            "chairman_qualitative_decision": "关键风险可通过交割条件缓释。",
            "threshold_result": "pass",
            "conditions": ["完成核心客户函证。"],
            "monitoring_metrics": ["核心客户续约率"],
            "decision": "pass",
            "score_delta_explanation": "主席定性调整与加权分数一致。",
            "executive_summary": "项目关键事实可核验，风险由明确交割条件约束。",
            "decision_rationale": "六维评分、红蓝对抗和证据链支持有条件通过。",
            "verified_facts": [{"claim_id": "CLM-R4-001", "evidence_ids": [evidence_id]}],
            "assumptions": [],
            "core_disputes": ["核心客户续约验证需在交割前完成。"],
            "principal_risks": [
                {"risk": "客户集中度", "evidence_ids": [evidence_id]}
            ],
            "valuation_and_exit": ["估值与退出假设须随证据快照持续复核。"],
            "parent_report_id": None,
            "created_at": CREATED_AT,
            "human_confirmation": {
                "status": "confirmed",
                "confirmed_by": {"id": 7, "username": "methodology-owner"},
                "confirmed_at": "2026-07-13T10:00:00Z",
            },
        },
    )
    report_sections = [
        "# 一级市场投决报告",
        "## 项目结论",
        "本项目在估值保护、客户续约验证与现金流阈值落实后给予有条件支持。",
        "## 证据充分性",
        "关键项目事实均绑定至有效 Evidence，并保留页码、来源版本与核验状态。",
        "## 财务分析",
        "收入、利润、现金流和估值测算均留存期间、单位、公式与计算轨迹。",
        "## 法律与合规",
        "股权、知识产权、资质和关联交易风险均形成交割前条件及责任人。",
        "## 风险与反证",
        "红蓝对抗覆盖客户集中、毛利压力与退出窗口，主席已经逐项裁定。",
        "## 投资条件",
        "交割前完成核心客户函证，并将收入集中度和经营现金流纳入持续监控。",
        "## 审批",
        "事实核查与投委会人工确认均已完成，审计链保留所有输入、裁定和输出摘要。",
    ]
    long_report = "\n\n".join(report_sections) + "\n" + ("完整分析正文。" * 50)
    report_path = bundle / "decision/IC_DECISION_REPORT.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(long_report, encoding="utf-8")
    _write_json(
        bundle / "decision/factcheck.json",
        {
            "schema_version": "siq_ic_report_factcheck_v1",
            "status": "pass",
            "claim_checks": [{"claim_id": "CLM-R4-001", "status": "pass"}],
            "numeric_checks": [{"claim_id": "CLM-R4-001", "status": "pass"}],
            "citation_checks": [{"claim_id": "CLM-R4-001", "status": "pass"}],
            "contradictions": [],
            "unsupported_claims": [],
            "required_repairs": [],
            "report_id": "ICRPT-R4-GATE-00000001",
            "report_revision": 1,
            "checked_at": "2026-07-13T09:50:00Z",
            "evidence_snapshot_hash": SNAPSHOT,
        },
    )
    _write_json(
        bundle / "decision/report_quality.json",
        {
            "schema_version": "siq_ic_report_quality_v1",
            "report_id": "ICRPT-R4-GATE-00000001",
            "report_revision": 1,
            "deal_id": "DEAL-GATE-001",
            "evidence_snapshot_hash": SNAPSHOT,
            "status": "pass",
            "allowed_for_human_confirmation": True,
            "blocking_reasons": [],
            "checks": [{"id": "factcheck.result", "status": "pass"}],
            "metrics": {"unknown_evidence_count": 0},
        },
    )
    _write_json(
        bundle / "release/real_smoke.json",
        {
            "schema_version": "siq_ic_real_smoke_result_v1",
            "deal_id": "DEAL-GATE-001",
            "evidence_snapshot_hash": SNAPSHOT,
            "execution_mode": "real",
            "status": "passed",
            "hermes_called": True,
            "run_id": "SMOKE-20260713-001",
            "completed_at": "2026-07-13T09:30:00Z",
            "agent_retrievals": rows,
        },
    )
    _write_v3_provenance(
        bundle,
        module,
        release_candidate_case_id=release_candidate_case_id,
    )
    return bundle


def _append_historical_terminal_task(
    bundle: Path,
    *,
    status: str,
    include_audit: bool = True,
    hermes_run_id: str | None = None,
) -> dict:
    tasks_path = bundle / "phases/ic_agent_tasks.json"
    task_store = json.loads(tasks_path.read_text(encoding="utf-8"))
    source = next(task for task in task_store["tasks"] if task["phase"] == "R3")
    task = deepcopy(source)
    input_digest = _digest(f"historical:{status}:{len(task_store['tasks'])}")
    task_id = f"ICTASK-{input_digest[:24].upper()}"
    run_id = hermes_run_id or f"run_historical_{status}_{len(task_store['tasks'])}"
    task.update(
        {
            "task_id": task_id,
            "input_digest": input_digest,
            "handoff_id": f"ICHANDOFF-{_digest(f'handoff:{task_id}')[:24].upper()}",
            "handoff_digest": _digest(f"handoff:{task_id}"),
            "status": status,
            "hermes_run_id": run_id,
            "hermes_run_ids": [run_id],
            "attempt_history": [],
        }
    )
    task["task_claim"] = {**task["task_claim"], "status": status, "attempt": 1}
    if status in {"succeeded", "stale_on_completion"}:
        raw_path = f"audit/ic_agent_outputs/{task_id}/{run_id}.txt"
        raw_file = bundle / raw_path
        raw_file.parent.mkdir(parents=True, exist_ok=True)
        raw_file.write_text(json.dumps(task["validated_output"], ensure_ascii=False) + "\n", encoding="utf-8")
        task.update(
            {
                "output_artifact_path": raw_path,
                "output_artifact_paths": [raw_path],
                "output_artifact_hash": _sha256(raw_file),
                "output_artifact_hashes": {raw_path: _sha256(raw_file)},
            }
        )
    else:
        task.update(
            {
                "output_artifact_paths": [],
                "output_artifact_hashes": {},
                "contract_validation": {
                    "passed": False,
                    "output_schema": task["output_schema"],
                    "error_type": "SupersededPlan",
                },
                "failure_reason": "superseded by the authoritative phase plan",
            }
        )
        task.pop("output_artifact_path", None)
        task.pop("output_artifact_hash", None)
        task.pop("validated_output", None)
    task_store["tasks"].append(task)
    _write_json(tasks_path, task_store)

    if include_audit:
        event = {
            "event_type": (
                "ic_phase_hermes_task_completed"
                if status in {"succeeded", "stale_on_completion"}
                else "ic_phase_hermes_task_failed"
            ),
            "workflow_run_id": task["workflow_run_id"],
            "task_id": task_id,
            "phase": task["phase"],
            "agent_id": task["agent_id"],
            "input_digest": input_digest,
            "handoff_digest": task["handoff_digest"],
            "hermes_run_id": run_id,
            "evidence_snapshot_hash": task["evidence_snapshot_hash"],
            "prompt_contract_version": task["prompt_contract_version"],
            "profile_contract_version": task["profile_contract_version"],
            "output_schema": task["output_schema"],
            "output_artifact_hashes": task["output_artifact_hashes"],
            "contract_validation": task["contract_validation"],
            "status": status,
        }
        for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            audit["events"].append(event)
            _write_json(audit_path, audit)
    return task


def _append_factcheck_prior_attempt(bundle: Path) -> tuple[dict, dict]:
    task_path = bundle / "decision/factcheck_task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    prior_run_id = "run_gate_factcheck_prior_001"
    prior_raw_path = f"audit/ic_agent_outputs/{task['task_id']}/{prior_run_id}.txt"
    prior_raw_file = bundle / prior_raw_path
    prior_raw_file.parent.mkdir(parents=True, exist_ok=True)
    prior_raw_file.write_text("invalid prior factcheck output\n", encoding="utf-8")
    prior_runtime = deepcopy(task["model_execution_audit"]["final_runtime"])
    prior_prompt_sha256 = _digest(f"prompt:{task['task_id']}:{prior_run_id}")
    prior_model_audit = {
        "schema_version": "siq_ic_model_execution_audit_v1",
        "runtime_metadata_status": "verified",
        "attempt_count": 1,
        "attempts": [
            {
                "hermes_run_id": prior_run_id,
                "purpose": "generation",
                "prompt_sha256": prior_prompt_sha256,
                "terminal_status": "succeeded",
                "runtime_metadata_status": "verified",
                "runtime": prior_runtime,
            }
        ],
        "final_hermes_run_id": prior_run_id,
        "final_prompt_sha256": prior_prompt_sha256,
        "final_runtime": prior_runtime,
    }
    prior_contract = {
        "passed": False,
        "output_schema": task["output_schema"],
        "error_type": "ValueError",
    }
    prior = {
        "lease_attempt": 1,
        "terminal_status": "failed",
        "started_at": "2026-07-13T09:40:00Z",
        "terminal_at": "2026-07-13T09:45:00Z",
        "hermes_run_id": prior_run_id,
        "hermes_run_ids": [prior_run_id],
        "output_artifact_path": prior_raw_path,
        "output_artifact_paths": [prior_raw_path],
        "output_artifact_hash": _sha256(prior_raw_file),
        "output_artifact_hashes": {prior_raw_path: _sha256(prior_raw_file)},
        "contract_validation": prior_contract,
        "model_execution_audit": prior_model_audit,
        "error": "factcheck output contract invalid",
    }
    task["task_claim"]["attempt"] = 2
    task["attempt_history"] = [prior]
    _write_json(task_path, task)
    prior_event = {
        "event_type": "ic_r4_factcheck_failed",
        "workflow_run_id": task["workflow_run_id"],
        "task_id": task["task_id"],
        "phase": "R4",
        "agent_id": "siq_factchecker",
        "report_id": task["report_id"],
        "report_revision": task["report_revision"],
        "input_digest": task["input_digest"],
        "hermes_run_id": prior_run_id,
        "evidence_snapshot_hash": task["evidence_snapshot_hash"],
        "prompt_contract_version": task["prompt_contract_version"],
        "profile_contract_version": task["profile_contract_version"],
        "output_schema": task["output_schema"],
        "output_artifact_hashes": deepcopy(prior["output_artifact_hashes"]),
        "contract_validation": deepcopy(prior_contract),
        "model_execution_audit": deepcopy(prior_model_audit),
        "status": "failed",
        "failure_reason": "ValueError",
    }
    for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit["events"].append(deepcopy(prior_event))
        _write_json(audit_path, audit)
    return task, prior


def test_default_manifest_covers_all_required_candidate_scenarios_without_accepting_them():
    module = _load_module()
    manifest = json.loads(module.DEFAULT_MANIFEST.read_text(encoding="utf-8"))

    result = module.validate_golden_manifest(manifest)

    assert result["passed"] is True
    assert set(result["coverage"]) == set(module.REQUIRED_GOLDEN_CASES)
    assert all(item["covered"] for item in result["coverage"].values())
    assert result["quality_accepted"] is False


def test_complete_bundle_is_release_eligible_but_gate_never_promotes_candidate(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    manifest_before = module.DEFAULT_MANIFEST.read_bytes()

    report = module.build_report(bundle=bundle)

    assert report["passed"] is True
    assert report["release_eligible"] is True
    assert report["manifest_candidate_status_preserved"] is True
    assert report["quality_accepted_written"] is False
    assert report["candidate_promotion_performed"] is False
    assert report["blocking_reasons"] == []
    metrics = report["bundle"]["metrics"]
    assert metrics["critical_claim_coverage"]["critical_coverage_ratio"] == 1.0
    assert metrics["numeric_trace"]["coverage_ratio"] == 1.0
    assert metrics["startup_retrieval"]["profile_count"] == 7
    assert metrics["real_smoke"]["routing"]["distinct_private_collections"] == 7
    assert module.DEFAULT_MANIFEST.read_bytes() == manifest_before


def test_gate_accepts_release_bundle_as_exactly_one_golden_candidate(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(
        tmp_path,
        module,
        release_candidate_case_id="GOLDEN-PMIC-CONDITIONAL-SUPPORT",
    )

    report = module.build_report(bundle=bundle)

    assert report["passed"] is True
    metric = report["bundle"]["metrics"]["golden_case_bindings"]
    assert metric["passed"] is True
    assert metric["distinct_run_count"] == 5
    assert metric["distinct_deal_count"] == 5
    assert sum(item["bundle_path"] == bundle.name for item in metric["cases"]) == 1


def test_gate_rejects_missing_required_field_under_exported_draft_2020_12_schema(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    r1_path = bundle / "phases/r1_reports.json"
    r1 = json.loads(r1_path.read_text(encoding="utf-8"))
    next(iter(r1.values())).pop("executive_summary")
    _write_json(r1_path, r1)

    report = module.build_report(bundle=bundle)

    schema_errors = report["bundle"]["metrics"]["schemas"]["errors"]
    assert report["passed"] is False
    assert any("siq_ic_expert_report_v2" in error and "executive_summary" in error for error in schema_errors)


def test_gate_rejects_schema_valid_artifact_tampered_after_task_validation(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    r1_path = bundle / "phases/r1_reports.json"
    r1 = json.loads(r1_path.read_text(encoding="utf-8"))
    tampered = next(iter(r1.values()))
    tampered["score"] = 1
    _write_json(r1_path, r1)

    report = module.build_report(bundle=bundle)

    execution_errors = report["bundle"]["metrics"]["execution_chain"]["errors"]
    assert report["passed"] is False
    assert any("validated_output_artifact_mismatch" in error for error in execution_errors)


def test_gate_recomputes_r4_scorecard_claim_references_after_synchronized_tamper(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    r4_path = bundle / "phases/r4_decision.json"
    r4 = json.loads(r4_path.read_text(encoding="utf-8"))
    r4["six_dimension_scorecard"][0]["claim_ids"] = ["CLM-R4-FORGED-UNKNOWN"]
    _write_json(r4_path, r4)

    tasks_path = bundle / "phases/ic_agent_tasks.json"
    task_store = json.loads(tasks_path.read_text(encoding="utf-8"))
    task = next(item for item in task_store["tasks"] if item["phase"] == "R4")
    task["validated_output"]["six_dimension_scorecard"] = deepcopy(r4["six_dimension_scorecard"])
    raw_path = bundle / task["output_artifact_paths"][0]
    raw_path.write_text(json.dumps(task["validated_output"], ensure_ascii=False) + "\n", encoding="utf-8")
    raw_digest = _sha256(raw_path)
    task["output_artifact_hash"] = raw_digest
    task["output_artifact_hashes"] = {task["output_artifact_paths"][0]: raw_digest}
    _write_json(tasks_path, task_store)
    for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        completion = next(
            event
            for event in audit["events"]
            if event.get("event_type") == "ic_phase_hermes_task_completed"
            and event.get("task_id") == task["task_id"]
        )
        completion["output_artifact_hashes"] = deepcopy(task["output_artifact_hashes"])
        _write_json(audit_path, audit)

    report = module.build_report(bundle=bundle)

    metrics = report["bundle"]["metrics"]
    semantic = metrics["r4_claim_cross_reference"]
    assert report["passed"] is False
    assert metrics["schemas"]["passed"] is True
    assert metrics["execution_chain"]["passed"] is True
    assert semantic["passed"] is False
    assert semantic["unknown_claim_ids"] == ["CLM-R4-FORGED-UNKNOWN"]
    assert "six_dimension_scorecard_unknown_claim_ids:CLM-R4-FORGED-UNKNOWN" in semantic["errors"]


def test_r4_cross_reference_metric_requires_six_unique_nonempty_dimensions(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    r4 = json.loads((bundle / "phases/r4_decision.json").read_text(encoding="utf-8"))
    r4["six_dimension_scorecard"][0]["claim_ids"] = []
    r4["six_dimension_scorecard"][1]["dimension"] = r4["six_dimension_scorecard"][0]["dimension"]
    r4["six_dimension_scorecard"].pop()

    metric = module._r4_claim_cross_reference_metric(r4)

    assert metric["passed"] is False
    assert "six_dimension_scorecard_cardinality_invalid" in metric["errors"]
    assert "six_dimension_scorecard_dimensions_not_unique" in metric["errors"]
    assert any(error.startswith("six_dimension_scorecard_claim_ids_missing:") for error in metric["errors"])


def test_gate_recomputes_model_runtime_identity_and_attempt_bindings(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    tasks_path = bundle / "phases/ic_agent_tasks.json"
    task_store = json.loads(tasks_path.read_text(encoding="utf-8"))
    task = task_store["tasks"][0]
    model_audit = task["model_execution_audit"]
    model_audit["attempt_count"] = 7
    model_audit["attempts"][0]["hermes_run_id"] = "run_forged_nested_identity"
    model_audit["attempts"][0]["prompt_sha256"] = "not-a-sha256"
    model_audit["attempts"][0]["runtime"]["effective"]["base_url"] = "https://secret.invalid/v1"
    model_audit["final_prompt_sha256"] = "a" * 64
    _write_json(tasks_path, task_store)
    for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        completion = next(event for event in audit["events"] if event.get("task_id") == task["task_id"])
        completion["model_execution_audit"] = deepcopy(model_audit)
        _write_json(audit_path, audit)

    report = module.build_report(bundle=bundle)

    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert report["passed"] is False
    assert "model_execution_attempt_count_mismatch" in errors
    assert "model_execution_attempt_run_mapping_invalid" in errors
    assert "model_execution_prompt_sha256_invalid:0" in errors
    assert "runtime_effective_fields_invalid" in errors
    assert "model_execution_final_prompt_sha256_mismatch" in errors
    assert "model_execution_final_runtime_mismatch" in errors


def test_gate_rejects_honestly_unverified_authoritative_runtime(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    tasks_path = bundle / "phases/ic_agent_tasks.json"
    task_store = json.loads(tasks_path.read_text(encoding="utf-8"))
    task = task_store["tasks"][0]
    model_audit = task["model_execution_audit"]
    model_audit["runtime_metadata_status"] = "unverified"
    model_audit["attempts"][0]["runtime_metadata_status"] = "unverified"
    model_audit["attempts"][0]["runtime"] = None
    model_audit["final_runtime"] = None
    _write_json(tasks_path, task_store)
    for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        completion = next(event for event in audit["events"] if event.get("task_id") == task["task_id"])
        completion["model_execution_audit"] = deepcopy(model_audit)
        _write_json(audit_path, audit)

    report = module.build_report(bundle=bundle)

    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert report["passed"] is False
    assert "model_execution_identity_unverified" in errors
    assert "runtime_missing_or_invalid" in errors


def test_gate_rejects_real_smoke_that_hides_verified_task_runtime(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    smoke_path = bundle / "release/real_smoke.json"
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    record = next(
        task
        for result in smoke["profile_results"].values()
        for task in result["tasks"]
    )
    task_id = record["task_id"]
    record.pop("model_execution_audit")
    _write_json(smoke_path, smoke)

    report = module.build_report(bundle=bundle)

    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert report["passed"] is False
    assert f"real_smoke:{task_id}:model_execution_identity_unverified" in errors
    assert f"real_smoke:{task_id}:model_execution_audit_mismatch" in errors


def test_gate_fails_closed_without_real_smoke_and_named_human_review(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    (bundle / "release/real_smoke.json").unlink()
    (bundle / "release/human_methodology_approval.json").unlink()
    r4_path = bundle / "phases/r4_decision.json"
    r4 = json.loads(r4_path.read_text(encoding="utf-8"))
    r4["human_confirmation"] = {"status": "pending"}
    _write_json(r4_path, r4)

    report = module.build_report(bundle=bundle)

    assert report["passed"] is False
    assert report["release_eligible"] is False
    assert report["quality_accepted_written"] is False
    blockers = "\n".join(report["blocking_reasons"])
    assert "real_smoke_missing_or_invalid" in blockers
    assert "human_methodology_approval_missing_or_invalid" in blockers
    assert "r4_human_confirmation_not_confirmed:pending" in blockers


def test_gate_reports_unknown_evidence_numeric_trace_fallback_dispute_and_report_hygiene(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    r2_path = bundle / "phases/r2_reports.json"
    r2 = json.loads(r2_path.read_text(encoding="utf-8"))
    first = next(iter(r2.values()))
    first["claims"][0]["evidence_ids"] = ["EVID-UNKNOWN"]
    first["claims"][0]["period"] = None
    first["generation_mode"] = "deterministic_recovery"
    _write_json(r2_path, r2)
    disputes_path = bundle / "phases/r1_5_disputes.json"
    disputes = json.loads(disputes_path.read_text(encoding="utf-8"))
    disputes["disputes"][0]["resolved"] = False
    disputes["disputes"][0]["status"] = "unresolved"
    _write_json(disputes_path, disputes)
    (bundle / "decision/IC_DECISION_REPORT.md").write_text(
        "# Report\n\nTODO: 请参见其他文件。\n/home/user/private/report.json\n",
        encoding="utf-8",
    )

    report = module.build_report(bundle=bundle)

    metrics = report["bundle"]["metrics"]
    assert report["passed"] is False
    assert metrics["evidence"]["unknown_ids"] == ["EVID-UNKNOWN"]
    assert metrics["critical_claim_coverage"]["critical_coverage_ratio"] < 1
    assert metrics["numeric_trace"]["coverage_ratio"] < 1
    assert metrics["fallback"]["detected"] is True
    assert metrics["unresolved_disputes"]["unresolved"] == 1
    assert metrics["report_hygiene"]["placeholder_count"] >= 1
    assert metrics["report_hygiene"]["internal_path_count"] >= 1


def test_gate_rejects_schema_spoof_and_cross_deal_attestations(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    r3_path = bundle / "phases/r3_reports.json"
    r3 = json.loads(r3_path.read_text(encoding="utf-8"))
    r3["schema_version"] = "siq_unrelated_but_prefixed_v1"
    _write_json(r3_path, r3)
    factcheck_path = bundle / "decision/factcheck.json"
    factcheck = json.loads(factcheck_path.read_text(encoding="utf-8"))
    factcheck["unknown_evidence_ids"] = ["EVID-CROSS-DEAL"]
    _write_json(factcheck_path, factcheck)
    smoke_path = bundle / "release/real_smoke.json"
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    smoke["deal_id"] = "DEAL-OTHER-001"
    _write_json(smoke_path, smoke)
    approval_path = bundle / "release/human_methodology_approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["deal_id"] = "DEAL-OTHER-001"
    _write_json(approval_path, approval)

    report = module.build_report(bundle=bundle)

    assert report["passed"] is False
    blockers = "\n".join(report["blocking_reasons"])
    assert "r3:schema_version_mismatch:siq_unrelated_but_prefixed_v1" in blockers
    assert "factcheck_unknown_evidence:1" in blockers
    assert "real_smoke_deal_id_mismatch" in blockers
    assert "human_methodology_approval_deal_id_mismatch" in blockers


def test_gate_rejects_legacy_phase_contracts_instead_of_treating_v1_as_authoritative(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)

    receipts_path = bundle / "phases/startup_receipts.json"
    receipts = json.loads(receipts_path.read_text(encoding="utf-8"))
    receipts["schema_version"] = "siq_ic_startup_receipts_v1"
    first_receipt = next(iter(receipts["agents"].values()))
    first_receipt["schema_version"] = "siq_ic_startup_retrieval_receipt_v1"
    _write_json(receipts_path, receipts)

    r1_path = bundle / "phases/r1_reports.json"
    r1 = json.loads(r1_path.read_text(encoding="utf-8"))
    next(iter(r1.values()))["schema_version"] = "siq_ic_r1_agent_report_v1"
    _write_json(r1_path, r1)

    disputes_path = bundle / "phases/r1_5_disputes.json"
    disputes = json.loads(disputes_path.read_text(encoding="utf-8"))
    disputes["disputes"][0]["chairman_ruling"]["schema_version"] = "siq_deal_r1_5_ruling_legacy_v0"
    _write_json(disputes_path, disputes)

    r4_path = bundle / "phases/r4_decision.json"
    r4 = json.loads(r4_path.read_text(encoding="utf-8"))
    r4["schema_version"] = "siq_ic_r4_decision_v1"
    _write_json(r4_path, r4)

    report = module.build_report(bundle=bundle)

    assert report["passed"] is False
    blockers = "\n".join(report["blocking_reasons"])
    assert "startup_receipts:schema_version_mismatch:siq_ic_startup_receipts_v1" in blockers
    assert "r1:report_schema_version_mismatch:siq_ic_r1_agent_report_v1" in blockers
    assert "r1_5:ruling_schema_version_mismatch:siq_deal_r1_5_ruling_legacy_v0" in blockers
    assert "r4:schema_version_mismatch:siq_ic_r4_decision_v1" in blockers


def test_gate_binds_quality_and_factcheck_to_current_r4_identity(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    quality_path = bundle / "decision/report_quality.json"
    quality = json.loads(quality_path.read_text(encoding="utf-8"))
    quality["report_id"] = "ICRPT-STALE-QUALITY-0001"
    quality["report_revision"] = 2
    _write_json(quality_path, quality)
    factcheck_path = bundle / "decision/factcheck.json"
    factcheck = json.loads(factcheck_path.read_text(encoding="utf-8"))
    factcheck["evidence_snapshot_hash"] = "b" * 64
    _write_json(factcheck_path, factcheck)

    report = module.build_report(bundle=bundle)

    assert report["passed"] is False
    blockers = "\n".join(report["blocking_reasons"])
    assert "report_quality_report_id_mismatch" in blockers
    assert "report_quality_report_revision_mismatch" in blockers
    assert "factcheck_snapshot_mismatch" in blockers


def test_gate_fails_closed_when_private_milvus_gate_or_quality_artifact_is_missing(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    receipts_path = bundle / "phases/startup_receipts.json"
    receipts = json.loads(receipts_path.read_text(encoding="utf-8"))
    profile_id, receipt = next(iter(receipts["agents"].items()))
    receipt["milvus_used"] = False
    receipt["gate"] = {"allowed_to_speak": False, "blocking_reasons": ["private_kb_empty"]}
    receipts["by_agent_phase"][profile_id][receipt["round_name"]] = receipt
    _write_json(receipts_path, receipts)
    (bundle / "decision/report_quality.json").unlink()

    report = module.build_report(bundle=bundle)

    assert report["passed"] is False
    blockers = "\n".join(report["blocking_reasons"])
    assert "artifact_missing:quality" in blockers
    assert f"{profile_id}:milvus_not_used" in blockers
    assert f"{profile_id}:private_background_gate_blocked" in blockers


def test_gate_rejects_non_v5_tasks_missing_methodology_and_tampered_raw_output(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    tasks_path = bundle / "phases/ic_agent_tasks.json"
    task_store = json.loads(tasks_path.read_text(encoding="utf-8"))
    task = task_store["tasks"][0]
    task["prompt_contract_version"] = "siq_ic_phase_prompt_v3"
    task["methodology_refs"] = []
    _write_json(tasks_path, task_store)
    raw_path = bundle / task["output_artifact_paths"][0]
    raw_path.write_text("tampered after completion\n", encoding="utf-8")

    report = module.build_report(bundle=bundle)

    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert report["passed"] is False
    assert "prompt_contract_version_not_v5" in errors
    assert "methodology_refs_missing" in errors
    assert "raw_output_digest_mismatch" in errors


def test_gate_validates_retry_attempt_lineage_and_failed_attempt_audit(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    tasks_path = bundle / "phases/ic_agent_tasks.json"
    task_store = json.loads(tasks_path.read_text(encoding="utf-8"))
    task = task_store["tasks"][0]
    task["task_claim"]["attempt"] = 2
    prior_prompt_sha256 = _digest("prompt:run_prior_timeout")
    prior_runtime = deepcopy(task["model_execution_audit"]["final_runtime"])
    prior_model_audit = {
        "schema_version": "siq_ic_model_execution_audit_v1",
        "runtime_metadata_status": "verified",
        "attempt_count": 1,
        "attempts": [
            {
                "hermes_run_id": "run_prior_timeout",
                "purpose": "generation",
                "prompt_sha256": prior_prompt_sha256,
                "terminal_status": "timed_out",
                "runtime_metadata_status": "verified",
                "runtime": prior_runtime,
            }
        ],
        "final_hermes_run_id": "run_prior_timeout",
        "final_prompt_sha256": prior_prompt_sha256,
        "final_runtime": prior_runtime,
    }
    task["attempt_history"] = [
        {
            "lease_attempt": 1,
            "terminal_status": "timed_out",
            "started_at": "2026-07-13T09:00:00Z",
            "terminal_at": "2026-07-13T09:05:00Z",
            "hermes_run_id": "run_prior_timeout",
            "hermes_run_ids": ["run_prior_timeout"],
            "output_artifact_path": None,
            "output_artifact_paths": [],
            "output_artifact_hash": None,
            "output_artifact_hashes": {},
            "contract_validation": {
                "passed": False,
                "output_schema": task["output_schema"],
                "error_type": "ICTaskWallClockTimeout",
            },
            "model_execution_audit": prior_model_audit,
            "error": "wall-clock timeout",
        }
    ]
    _write_json(tasks_path, task_store)
    audit_path = bundle / "phases/audit_log.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    audit["events"].append(
        {
            "event_type": "ic_phase_hermes_task_failed",
            "workflow_run_id": task["workflow_run_id"],
            "task_id": task["task_id"],
            "phase": task["phase"],
            "agent_id": task["agent_id"],
            "input_digest": task["input_digest"],
            "handoff_digest": task["handoff_digest"],
            "hermes_run_id": "run_prior_timeout",
            "evidence_snapshot_hash": SNAPSHOT,
            "prompt_contract_version": task["prompt_contract_version"],
            "profile_contract_version": task["profile_contract_version"],
            "output_schema": task["output_schema"],
            "output_artifact_hashes": {},
            "contract_validation": task["attempt_history"][0]["contract_validation"],
            "model_execution_audit": prior_model_audit,
            "status": "timed_out",
            "failure_reason": "ICTaskWallClockTimeout",
        }
    )
    _write_json(audit_path, audit)
    _write_json(bundle / "audit/audit_log.json", audit)

    report = module.build_report(bundle=bundle)

    execution = report["bundle"]["metrics"]["execution_chain"]
    assert report["passed"] is True
    assert execution["prior_attempt_count"] == 1

    prior = task_store["tasks"][0]["attempt_history"][0]
    prior.pop("model_execution_audit")
    for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
        audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
        prior_event = next(
            event
            for event in audit_payload["events"]
            if event.get("task_id") == task["task_id"] and event.get("status") == "timed_out"
        )
        prior_event.pop("model_execution_audit")
        _write_json(audit_path, audit_payload)
    _write_json(tasks_path, task_store)
    report = module.build_report(bundle=bundle)
    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert "attempt_history_model_execution:1:model_execution_audit_missing" in errors

    prior["model_execution_audit"] = deepcopy(prior_model_audit)
    prior["model_execution_audit"]["attempts"][0]["runtime"] = None
    for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
        audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
        prior_event = next(
            event
            for event in audit_payload["events"]
            if event.get("task_id") == task["task_id"] and event.get("status") == "timed_out"
        )
        prior_event["model_execution_audit"] = deepcopy(prior["model_execution_audit"])
        _write_json(audit_path, audit_payload)
    _write_json(tasks_path, task_store)
    report = module.build_report(bundle=bundle)
    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert "attempt_history_model_execution:1:model_execution_identity_unverified" in errors

    task_store["tasks"][0]["attempt_history"][0]["lease_attempt"] = 7
    _write_json(tasks_path, task_store)
    report = module.build_report(bundle=bundle)
    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert report["passed"] is False
    assert "attempt_history_sequence_invalid" in errors


def test_gate_maps_task_retry_raw_output_to_each_model_terminal_status(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    tasks_path = bundle / "phases/ic_agent_tasks.json"
    task_store = json.loads(tasks_path.read_text(encoding="utf-8"))
    task = task_store["tasks"][0]
    generation_run_id = "run_prior_generation_succeeded"
    repair_run_id = "run_prior_repair_failed"
    generation_raw_path = (
        f"audit/ic_agent_outputs/{task['task_id']}/{generation_run_id}.txt"
    )
    generation_raw_file = bundle / generation_raw_path
    generation_raw_file.parent.mkdir(parents=True, exist_ok=True)
    generation_raw_file.write_text("invalid generation output\n", encoding="utf-8")
    runtime = deepcopy(task["model_execution_audit"]["final_runtime"])
    attempts = [
        {
            "hermes_run_id": generation_run_id,
            "purpose": "generation",
            "prompt_sha256": _digest(f"prompt:{generation_run_id}"),
            "terminal_status": "succeeded",
            "runtime_metadata_status": "verified",
            "runtime": deepcopy(runtime),
        },
        {
            "hermes_run_id": repair_run_id,
            "purpose": "contract_repair",
            "prompt_sha256": _digest(f"prompt:{repair_run_id}"),
            "terminal_status": "failed",
            "runtime_metadata_status": "verified",
            "runtime": deepcopy(runtime),
        },
    ]
    model_audit = {
        "schema_version": "siq_ic_model_execution_audit_v1",
        "runtime_metadata_status": "verified",
        "attempt_count": 2,
        "attempts": attempts,
        "final_hermes_run_id": repair_run_id,
        "final_prompt_sha256": attempts[-1]["prompt_sha256"],
        "final_runtime": deepcopy(runtime),
    }
    contract_validation = {
        "passed": False,
        "output_schema": task["output_schema"],
        "error_type": "RunTerminalError",
    }
    prior = {
        "lease_attempt": 1,
        "terminal_status": "failed",
        "started_at": "2026-07-13T09:00:00Z",
        "terminal_at": "2026-07-13T09:05:00Z",
        "hermes_run_id": repair_run_id,
        "hermes_run_ids": [generation_run_id, repair_run_id],
        "output_artifact_path": generation_raw_path,
        "output_artifact_paths": [generation_raw_path],
        "output_artifact_hash": _sha256(generation_raw_file),
        "output_artifact_hashes": {generation_raw_path: _sha256(generation_raw_file)},
        "contract_validation": contract_validation,
        "model_execution_audit": model_audit,
        "error": "repair run failed before producing output",
    }
    task["task_claim"]["attempt"] = 2
    task["attempt_history"] = [prior]
    _write_json(tasks_path, task_store)
    terminal_event = {
        "event_type": "ic_phase_hermes_task_failed",
        "workflow_run_id": task["workflow_run_id"],
        "task_id": task["task_id"],
        "phase": task["phase"],
        "agent_id": task["agent_id"],
        "input_digest": task["input_digest"],
        "handoff_digest": task["handoff_digest"],
        "hermes_run_id": repair_run_id,
        "evidence_snapshot_hash": task["evidence_snapshot_hash"],
        "prompt_contract_version": task["prompt_contract_version"],
        "profile_contract_version": task["profile_contract_version"],
        "output_schema": task["output_schema"],
        "output_artifact_hashes": deepcopy(prior["output_artifact_hashes"]),
        "contract_validation": deepcopy(contract_validation),
        "model_execution_audit": deepcopy(model_audit),
        "status": "failed",
        "failure_reason": "RunTerminalError",
    }
    for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        audit["events"].append(deepcopy(terminal_event))
        _write_json(audit_path, audit)

    assert module.build_report(bundle=bundle)["passed"] is True

    prior["output_artifact_path"] = None
    prior["output_artifact_paths"] = []
    prior["output_artifact_hash"] = None
    prior["output_artifact_hashes"] = {}
    _write_json(tasks_path, task_store)
    for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        event = next(
            item
            for item in audit["events"]
            if item.get("task_id") == task["task_id"] and item.get("status") == "failed"
        )
        event["output_artifact_hashes"] = {}
        _write_json(audit_path, audit)

    report = module.build_report(bundle=bundle)

    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert report["passed"] is False
    assert f"attempt_history_raw_output_cardinality_invalid:{generation_run_id}" in errors
    assert f"attempt_history_raw_output_cardinality_invalid:{repair_run_id}" not in errors


def test_gate_counts_only_artifact_bound_tasks_and_audits_superseded_terminal_tasks(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    baseline = module.build_report(bundle=bundle)["bundle"]["metrics"]["execution_chain"]
    _append_historical_terminal_task(bundle, status="succeeded")
    _append_historical_terminal_task(bundle, status="cancelled")
    _append_historical_terminal_task(bundle, status="stale_on_completion")

    report = module.build_report(bundle=bundle)

    execution = report["bundle"]["metrics"]["execution_chain"]
    assert report["passed"] is True
    assert execution["authoritative_task_count"] == baseline["authoritative_task_count"]
    assert execution["successful_task_count"] == baseline["successful_task_count"]
    assert execution["validated_task_count"] == baseline["validated_task_count"]
    assert execution["historical_terminal_task_count"] == 3
    assert execution["historical_succeeded_task_count"] == 1
    assert execution["historical_stale_task_count"] == 1
    assert execution["historical_failed_task_count"] == 1


def test_gate_rejects_unaudited_historical_failure_and_failed_authoritative_binding(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    historical = _append_historical_terminal_task(bundle, status="failed", include_audit=False)

    report = module.build_report(bundle=bundle)

    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert report["passed"] is False
    assert f"historical:{historical['task_id']}:terminal_audit_missing" in errors

    tasks_path = bundle / "phases/ic_agent_tasks.json"
    task_store = json.loads(tasks_path.read_text(encoding="utf-8"))
    authoritative = next(task for task in task_store["tasks"] if task["phase"] == "R0")
    authoritative["status"] = "failed"
    _write_json(tasks_path, task_store)

    report = module.build_report(bundle=bundle)
    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert f"authoritative_task_not_succeeded:{authoritative['task_id']}:failed" in errors


def test_gate_rejects_run_identity_reused_by_unbound_success(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    task_store = json.loads((bundle / "phases/ic_agent_tasks.json").read_text(encoding="utf-8"))
    authoritative_run_id = task_store["tasks"][0]["hermes_run_id"]
    _append_historical_terminal_task(
        bundle,
        status="succeeded",
        hermes_run_id=authoritative_run_id,
    )

    report = module.build_report(bundle=bundle)

    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert report["passed"] is False
    assert f"hermes_run_id_reused:{authoritative_run_id}" in errors


def test_gate_recomputes_handoff_sidecar_and_requires_identical_double_audit(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    handoffs_path = bundle / "phases/ic_agent_handoffs.json"
    handoff_store = json.loads(handoffs_path.read_text(encoding="utf-8"))
    handoff_id = handoff_store["handoffs"][0]["handoff_id"]
    handoff_store["payloads"][handoff_id]["payload"] = {"tampered": True}
    _write_json(handoffs_path, handoff_store)
    durable_audit_path = bundle / "audit/audit_log.json"
    durable_audit = json.loads(durable_audit_path.read_text(encoding="utf-8"))
    durable_audit["events"].pop()
    _write_json(durable_audit_path, durable_audit)

    report = module.build_report(bundle=bundle)

    execution_errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    audit_errors = report["bundle"]["metrics"]["double_audit"]["errors"]
    assert report["passed"] is False
    assert "handoff_sidecar_binding_invalid" in execution_errors
    assert "audit_logs_diverged" in audit_errors


def test_gate_binds_factcheck_task_raw_output_and_business_status(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    task_path = bundle / "decision/factcheck_task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    raw_path = bundle / task["output_artifact_paths"][0]
    raw_path.write_text("tampered factcheck output\n", encoding="utf-8")
    phase_audit_path = bundle / "phases/audit_log.json"
    phase_audit = json.loads(phase_audit_path.read_text(encoding="utf-8"))
    event = next(item for item in phase_audit["events"] if item.get("event_type") == "ic_r4_factcheck_completed")
    event["factcheck_status"] = "warn"
    _write_json(phase_audit_path, phase_audit)
    _write_json(bundle / "audit/audit_log.json", phase_audit)

    report = module.build_report(bundle=bundle)

    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert report["passed"] is False
    assert "factcheck:raw_output_digest_mismatch" in errors
    assert "factcheck:completion_audit_missing" in errors


def test_gate_recomputes_factchecker_runtime_and_binds_completion_audit(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    task_path = bundle / "decision/factcheck_task.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    model_audit = task["model_execution_audit"]
    model_audit["attempts"][0]["runtime"]["effective"]["base_url"] = "https://secret.invalid/v1"
    model_audit["final_runtime"] = deepcopy(model_audit["attempts"][0]["runtime"])
    _write_json(task_path, task)
    for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        completion = next(
            event for event in audit["events"] if event.get("event_type") == "ic_r4_factcheck_completed"
        )
        completion["model_execution_audit"] = deepcopy(model_audit)
        _write_json(audit_path, audit)

    report = module.build_report(bundle=bundle)

    execution = report["bundle"]["metrics"]["execution_chain"]
    errors = "\n".join(execution["errors"])
    assert report["passed"] is False
    assert execution["factcheck_runtime_verified"] is False
    assert "factcheck:model_execution_attempt:0:runtime_effective_fields_invalid" in errors
    assert "factcheck:model_execution_identity_unverified" in errors


def test_gate_validates_factchecker_retry_history_and_rejects_lineage_tampering(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    task, prior = _append_factcheck_prior_attempt(bundle)

    report = module.build_report(bundle=bundle)

    execution = report["bundle"]["metrics"]["execution_chain"]
    assert report["passed"] is True
    assert execution["factcheck_prior_attempt_count"] == 1

    phase_tasks = json.loads((bundle / "phases/ic_agent_tasks.json").read_text(encoding="utf-8"))
    reused_run_id = phase_tasks["tasks"][0]["hermes_run_id"]
    prior["lease_attempt"] = 7
    prior["hermes_run_ids"].append(reused_run_id)
    prior_model_audit = prior["model_execution_audit"]
    prior_model_audit["attempt_count"] = 7
    prior_model_audit["attempts"][0]["prompt_sha256"] = "not-a-sha256"
    prior_model_audit["attempts"][0]["runtime"] = None
    prior_raw_path = bundle / prior["output_artifact_paths"][0]
    prior_raw_path.write_text("tampered prior raw output\n", encoding="utf-8")
    _write_json(bundle / "decision/factcheck_task.json", task)
    for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        prior_event = next(
            event
            for event in audit["events"]
            if event.get("event_type") == "ic_r4_factcheck_failed"
        )
        prior_event["workflow_run_id"] = "ICRUN-FORGED-SESSION"
        prior_event["model_execution_audit"] = deepcopy(prior_model_audit)
        _write_json(audit_path, audit)

    report = module.build_report(bundle=bundle)

    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert report["passed"] is False
    assert "factcheck:attempt_history_sequence_invalid" in errors
    assert f"factcheck:attempt_history_run_id_not_unique:{reused_run_id}" in errors
    assert "factcheck:attempt_history_raw_output_invalid:" in errors
    assert "factcheck:attempt_history_model_execution:7:model_execution_attempt_count_mismatch" in errors
    assert "factcheck:attempt_history_model_execution:7:model_execution_prompt_sha256_invalid:0" in errors
    assert "factcheck:attempt_history_model_execution:7:model_execution_identity_unverified" in errors
    assert "factcheck:attempt_history_terminal_audit_missing" in errors


def test_gate_maps_factcheck_retry_raw_output_to_each_model_terminal_status(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    task, prior = _append_factcheck_prior_attempt(bundle)
    repair_run_id = "run_gate_factcheck_prior_repair_failed"
    runtime = deepcopy(prior["model_execution_audit"]["final_runtime"])
    repair_attempt = {
        "hermes_run_id": repair_run_id,
        "purpose": "contract_repair",
        "prompt_sha256": _digest(f"prompt:{task['task_id']}:{repair_run_id}"),
        "terminal_status": "failed",
        "runtime_metadata_status": "verified",
        "runtime": deepcopy(runtime),
    }
    prior["hermes_run_id"] = repair_run_id
    prior["hermes_run_ids"].append(repair_run_id)
    prior_model_audit = prior["model_execution_audit"]
    prior_model_audit["attempt_count"] = 2
    prior_model_audit["attempts"].append(repair_attempt)
    prior_model_audit["final_hermes_run_id"] = repair_run_id
    prior_model_audit["final_prompt_sha256"] = repair_attempt["prompt_sha256"]
    prior_model_audit["final_runtime"] = deepcopy(runtime)
    _write_json(bundle / "decision/factcheck_task.json", task)
    for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        event = next(
            item
            for item in audit["events"]
            if item.get("event_type") == "ic_r4_factcheck_failed"
        )
        event["hermes_run_id"] = repair_run_id
        event["model_execution_audit"] = deepcopy(prior_model_audit)
        _write_json(audit_path, audit)

    assert module.build_report(bundle=bundle)["passed"] is True

    repair_raw_path = (
        f"audit/ic_agent_outputs/{task['task_id']}/{repair_run_id}-repair-1.txt"
    )
    repair_raw_file = bundle / repair_raw_path
    repair_raw_file.write_text("unexpected output for failed repair\n", encoding="utf-8")
    prior["output_artifact_paths"].append(repair_raw_path)
    prior["output_artifact_hashes"][repair_raw_path] = _sha256(repair_raw_file)
    _write_json(bundle / "decision/factcheck_task.json", task)
    for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        event = next(
            item
            for item in audit["events"]
            if item.get("event_type") == "ic_r4_factcheck_failed"
        )
        event["output_artifact_hashes"] = deepcopy(prior["output_artifact_hashes"])
        _write_json(audit_path, audit)

    report = module.build_report(bundle=bundle)

    errors = "\n".join(report["bundle"]["metrics"]["execution_chain"]["errors"])
    assert report["passed"] is False
    assert (
        f"factcheck:attempt_history_raw_output_cardinality_invalid:{repair_run_id}"
        in errors
    )


def test_gate_binds_human_approval_to_exact_report_confirmation_and_audit(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    approval_path = bundle / "release/human_methodology_approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["report_binding"]["revision"] = 2
    approval["human_confirmation_binding"]["confirmed_by"] = {
        "id": 99,
        "username": "different-reviewer",
    }
    _write_json(approval_path, approval)
    r4_path = bundle / "phases/r4_decision.json"
    r4 = json.loads(r4_path.read_text(encoding="utf-8"))
    r4["decision"] = "reject"
    _write_json(r4_path, r4)

    report = module.build_report(bundle=bundle)

    errors = report["bundle"]["metrics"]["human_methodology_approval"]["errors"]
    confirmation_errors = report["bundle"]["metrics"]["human_confirmation"]["errors"]
    assert report["passed"] is False
    assert "human_methodology_approval_revision_mismatch" in errors
    assert "human_methodology_approval_confirmation_confirmed_by_mismatch" in errors
    assert "r4_human_confirmation_decision_sha256_mismatch" in confirmation_errors


def test_gate_rejects_unidentified_methodology_approver(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    approval_path = bundle / "release/human_methodology_approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["approved_by"] = "unattributed-reviewer"
    _write_json(approval_path, approval)

    report = module.build_report(bundle=bundle)

    approval_errors = report["bundle"]["metrics"]["human_methodology_approval"]["errors"]
    assert report["passed"] is False
    assert "human_methodology_approval_actor_missing" in approval_errors


def test_gate_rejects_methodology_approver_matching_r4_confirmer(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    approval_path = bundle / "release/human_methodology_approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["approved_by"] = {"id": 7, "name": "R4 Confirmer"}
    _write_json(approval_path, approval)

    report = module.build_report(bundle=bundle)

    approval_errors = report["bundle"]["metrics"]["human_methodology_approval"]["errors"]
    assert report["passed"] is False
    assert report["bundle"]["release_eligible"] is False
    assert "human_methodology_approval_actor_not_independent" in approval_errors


def test_gate_rejects_legacy_approved_confirmation_aliases(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    decision_path = bundle / "phases/r4_decision.json"
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    confirmation = decision["human_confirmation"]
    confirmation["status"] = "approved"
    confirmation["approved_by"] = confirmation.pop("confirmed_by")
    _write_json(decision_path, decision)

    report = module.build_report(bundle=bundle)

    confirmation_errors = report["bundle"]["metrics"]["human_confirmation"]["errors"]
    assert report["passed"] is False
    assert report["bundle"]["release_eligible"] is False
    assert "r4_human_confirmation_not_confirmed:approved" in confirmation_errors
    assert "r4_human_confirmation_approved_by_forbidden" in confirmation_errors
    assert "r4_human_confirmation_trusted_actor_missing" in confirmation_errors


def test_gate_requires_methodology_approval_strictly_after_confirmation_and_audit(tmp_path):
    module = _load_module()
    cases = (
        ("confirmed_at", "human_methodology_approval_not_after_confirmation"),
        ("audit_event_created_at", "human_methodology_approval_not_after_confirmation_audit"),
    )
    for timestamp_key, expected_error in cases:
        bundle = _make_complete_bundle(tmp_path / timestamp_key, module)
        approval_path = bundle / "release/human_methodology_approval.json"
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        approval["approved_at"] = approval["human_confirmation_binding"][timestamp_key]
        _write_json(approval_path, approval)

        report = module.build_report(bundle=bundle)

        approval_errors = report["bundle"]["metrics"]["human_methodology_approval"]["errors"]
        assert report["passed"] is False
        assert report["bundle"]["release_eligible"] is False
        assert expected_error in approval_errors


def test_gate_rejects_reused_or_tampered_golden_case_bindings(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    bindings_path = bundle / "release/golden_case_bindings.json"
    payload = json.loads(bindings_path.read_text(encoding="utf-8"))
    payload["bindings"][1]["run_id"] = payload["bindings"][0]["run_id"]
    first = payload["bindings"][0]
    result_path = bundle.parent / first["bundle_path"] / first["result_path"]
    result = json.loads(result_path.read_text(encoding="utf-8"))
    first_path = next(iter(result["path_results"].values()))
    artifact_path = result_path.parents[1] / first_path["artifact_path"]
    artifact_path.write_text('{"status":"tampered"}\n', encoding="utf-8")
    _write_json(bindings_path, payload)

    report = module.build_report(bundle=bundle)

    errors = "\n".join(report["bundle"]["metrics"]["golden_case_bindings"]["errors"])
    assert report["passed"] is False
    assert "run_id_not_independent" in errors
    assert "path_artifact_digest_mismatch" in errors


def test_gate_rejects_repeated_golden_candidate_bundle_path(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(
        tmp_path,
        module,
        release_candidate_case_id="GOLDEN-PMIC-CONDITIONAL-SUPPORT",
    )
    bindings_path = bundle / "release/golden_case_bindings.json"
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    self_binding = next(item for item in bindings["bindings"] if item["bundle_path"] == bundle.name)
    other_binding = next(item for item in bindings["bindings"] if item is not self_binding)
    other_binding["bundle_path"] = self_binding["bundle_path"]
    _write_json(bindings_path, bindings)
    approval_path = bundle / "release/human_methodology_approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["golden_case_bindings_sha256"] = _sha256(bindings_path)
    _write_json(approval_path, approval)

    report = module.build_report(bundle=bundle)

    errors = "\n".join(report["bundle"]["metrics"]["golden_case_bindings"]["errors"])
    assert report["passed"] is False
    assert "bundle_path_not_independent" in errors
    assert "case_bundle_not_independent" in errors


def test_gate_rejects_post_approval_replacement_of_same_named_golden_suite(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    bindings_path = bundle / "release/golden_case_bindings.json"
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    binding = bindings["bindings"][0]
    result_path = bundle.parent / binding["bundle_path"] / binding["result_path"]
    result = json.loads(result_path.read_text(encoding="utf-8"))
    path_result = next(iter(result["path_results"].values()))
    artifact_path = result_path.parents[1] / path_result["artifact_path"]
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    artifact["assertions"][0].update(
        {
            "name": "post-approval-replacement",
            "expected": "replacement",
            "actual": "replacement",
        }
    )
    _write_json(artifact_path, artifact)
    path_result["artifact_sha256"] = _sha256(artifact_path)
    _write_json(result_path, result)
    binding["result_sha256"] = _sha256(result_path)
    _write_json(bindings_path, bindings)

    report = module.build_report(bundle=bundle)

    approval_errors = report["bundle"]["metrics"]["human_methodology_approval"]["errors"]
    assert report["passed"] is False
    assert "human_methodology_approval_golden_bindings_digest_mismatch" in approval_errors


def test_gate_rejects_wrong_phase_schema_artifact_metadata_and_raw_manifest(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    tasks_path = bundle / "phases/ic_agent_tasks.json"
    task_store = json.loads(tasks_path.read_text(encoding="utf-8"))
    task = next(item for item in task_store["tasks"] if item["phase"] == "R1A")
    task["output_schema"] = "siq_ic_claim_v1"
    task["contract_validation"].update(
        {
            "output_schema": "siq_ic_claim_v1",
            "artifact_schema": "siq_ic_claim_v1",
        }
    )
    task["output_artifact_hashes"]["audit/ic_agent_outputs/unbound.txt"] = "a" * 64
    _write_json(tasks_path, task_store)

    reports_path = bundle / "phases/r1_reports.json"
    reports = json.loads(reports_path.read_text(encoding="utf-8"))
    report = next(item for item in reports.values() if item["task_id"] != task["task_id"])
    report["input_digest"] = "f" * 64
    _write_json(reports_path, reports)

    for audit_path in (bundle / "phases/audit_log.json", bundle / "audit/audit_log.json"):
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        event = next(item for item in audit["events"] if item.get("task_id") == task["task_id"])
        event["output_schema"] = task["output_schema"]
        event["contract_validation"] = deepcopy(task["contract_validation"])
        event["output_artifact_hashes"] = deepcopy(task["output_artifact_hashes"])
        _write_json(audit_path, audit)

    smoke_path = bundle / "release/real_smoke.json"
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    record = next(
        item for item in smoke["profile_results"][task["agent_id"]]["tasks"] if item["task_id"] == task["task_id"]
    )
    record["contract_validation"]["artifact_schema"] = "siq_ic_claim_v1"
    _write_json(smoke_path, smoke)

    result = module.build_report(bundle=bundle)

    errors = "\n".join(result["bundle"]["metrics"]["execution_chain"]["errors"])
    assert result["passed"] is False
    assert "phase_output_schema_invalid" in errors
    assert "contract_artifact_schema_mismatch" in errors
    assert "raw_output_manifest_mismatch" in errors
    assert "input_digest_mismatch" in errors


def test_gate_reconstructs_r15_split_artifact_and_rejects_shared_field_drift(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    disputes_path = bundle / "phases/r1_5_disputes.json"
    disputes = json.loads(disputes_path.read_text(encoding="utf-8"))
    dispute = disputes["disputes"][0]
    assert {"question", "positions", "evidence_snapshot_hash"}.isdisjoint(dispute["chairman_ruling"])
    assert module.build_report(bundle=bundle)["passed"] is True
    dispute["question"] = "tampered after validated task completion"
    dispute["chairman_ruling"]["submission_schema_version"] = "siq_unrelated_schema_v1"
    _write_json(disputes_path, disputes)

    result = module.build_report(bundle=bundle)

    errors = "\n".join(result["bundle"]["metrics"]["execution_chain"]["errors"])
    assert result["passed"] is False
    assert "canonical_artifact_mismatch:question" in errors
    assert "submission_schema_version_mismatch" in errors


def test_gate_rejects_run_alias_and_duplicate_workflow_identity(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    tasks_path = bundle / "phases/ic_agent_tasks.json"
    task_store = json.loads(tasks_path.read_text(encoding="utf-8"))
    task = task_store["tasks"][0]
    task["hermes_run_ids"].append(task["hermes_run_id"][:-1])
    _write_json(tasks_path, task_store)
    runs_path = bundle / "phases/ic_workflow_runs.json"
    run_store = json.loads(runs_path.read_text(encoding="utf-8"))
    run_store["runs"].append(deepcopy(run_store["runs"][0]))
    _write_json(runs_path, run_store)

    result = module.build_report(bundle=bundle)

    errors = "\n".join(result["bundle"]["metrics"]["execution_chain"]["errors"])
    assert result["passed"] is False
    assert "workflow_run_ids_not_unique" in errors
    assert "raw_output_cardinality_invalid" in errors


def test_gate_rejects_untrusted_evaluator_and_reused_golden_path_artifact(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    bindings_path = bundle / "release/golden_case_bindings.json"
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    binding = bindings["bindings"][0]
    result_path = bundle.parent / binding["bundle_path"] / binding["result_path"]
    golden_result = json.loads(result_path.read_text(encoding="utf-8"))
    golden_result["evaluator"] = {
        "name": "self-asserted-evaluator",
        "version": "v999",
        "deterministic_checks": True,
    }
    first_path_result = next(iter(golden_result["path_results"].values()))
    for path_name in golden_result["path_results"]:
        golden_result["path_results"][path_name] = deepcopy(first_path_result)
    _write_json(result_path, golden_result)
    binding["result_sha256"] = _sha256(result_path)
    _write_json(bindings_path, bindings)
    approval_path = bundle / "release/human_methodology_approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["golden_case_bindings_sha256"] = _sha256(bindings_path)
    _write_json(approval_path, approval)

    result = module.build_report(bundle=bundle)

    errors = "\n".join(result["bundle"]["metrics"]["golden_case_bindings"]["errors"])
    assert result["passed"] is False
    assert "result_evaluator_invalid" in errors
    assert "path_artifact_not_independent" in errors


def test_gate_rejects_noncanonical_golden_bundle_alias(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    bindings_path = bundle / "release/golden_case_bindings.json"
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    binding = bindings["bindings"][0]
    binding["bundle_path"] = f"{binding['bundle_path']}/../{binding['bundle_path']}"
    _write_json(bindings_path, bindings)
    approval_path = bundle / "release/human_methodology_approval.json"
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    approval["golden_case_bindings_sha256"] = _sha256(bindings_path)
    _write_json(approval_path, approval)

    result = module.build_report(bundle=bundle)

    errors = "\n".join(result["bundle"]["metrics"]["golden_case_bindings"]["errors"])
    assert result["passed"] is False
    assert "case_bundle_path_not_canonical" in errors


def test_gate_rejects_required_artifact_symlink_escape(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    quality_path = bundle / "decision/report_quality.json"
    escaped_path = tmp_path / "escaped-quality.json"
    quality_path.replace(escaped_path)
    quality_path.symlink_to(escaped_path)

    result = module.build_report(bundle=bundle)

    assert result["passed"] is False
    assert "artifact_missing:quality" in result["blocking_reasons"]


def test_cli_writes_json_and_markdown_and_uses_fail_closed_exit_code(tmp_path):
    module = _load_module()
    bundle = _make_complete_bundle(tmp_path, module)
    output_json = tmp_path / "gate.json"
    output_markdown = tmp_path / "gate.md"

    exit_code = module.main(
        [
            "--bundle",
            str(bundle),
            "--output-json",
            str(output_json),
            "--output-markdown",
            str(output_markdown),
        ]
    )

    assert exit_code == 0
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    markdown = output_markdown.read_text(encoding="utf-8")
    assert payload["schema_version"] == module.REPORT_SCHEMA
    assert payload["passed"] is True
    assert "| `real_smoke` | PASS | `7` |" in markdown
    assert "Candidate promotion performed: `false`" in markdown

    (bundle / "release/real_smoke.json").unlink()
    assert (
        module.main(
            [
                "--bundle",
                str(bundle),
                "--output-json",
                str(output_json),
                "--output-markdown",
                str(output_markdown),
            ]
        )
        == 1
    )
