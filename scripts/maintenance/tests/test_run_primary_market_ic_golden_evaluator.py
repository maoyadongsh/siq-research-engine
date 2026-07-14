from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
EVALUATOR_PATH = REPO_ROOT / "scripts/maintenance/run_primary_market_ic_golden_evaluator.py"
RELEASE_GATE_PATH = REPO_ROOT / "scripts/maintenance/run_primary_market_ic_release_gate.py"
CASE_IDS = {
    "GOLDEN-PMIC-CONDITIONAL-SUPPORT",
    "GOLDEN-PMIC-MATERIAL-RISK",
    "GOLDEN-PMIC-INSUFFICIENT-EVIDENCE",
    "GOLDEN-PMIC-FULL-R3",
    "GOLDEN-PMIC-SNAPSHOT-STALE",
}


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(path: Path, case_ids: set[str]) -> Path:
    _write_json(
        path,
        {
            "schema_version": "siq_ic_golden_case_manifest_v1",
            "acceptance_status": "candidates_only",
            "quality_accepted": False,
            "cases": [
                {
                    "case_id": case_id,
                    "scenario": f"offline evaluator fixture for {case_id}",
                    "status": "candidate",
                    "quality_accepted": False,
                    "required_paths": ["source-activation"],
                    "known_gap": "Candidate fixture has not received methodology approval.",
                }
                for case_id in sorted(case_ids)
            ],
        },
    )
    return path


def _bundle(
    root: Path,
    *,
    deal_id: str,
    run_id: str,
    snapshot_hash: str,
    smoke_snapshot_hash: str | None = None,
    include_source_registry: bool = True,
) -> Path:
    bundle = root / deal_id
    _write_json(
        bundle / "manifest.json",
        {
            "schema_version": "siq_deal_manifest_v1",
            "deal_id": deal_id,
            "documents": [],
        },
    )
    _write_json(
        bundle / "evidence/evidence_snapshot.json",
        {
            "schema_version": "siq_deal_evidence_snapshot_v1",
            "deal_id": deal_id,
            "snapshot_hash": snapshot_hash,
            "source_ids": [f"SRC-{deal_id}"],
        },
    )
    if include_source_registry:
        _write_json(
            bundle / "sources/analysis_sources.json",
            {
                "schema_version": "siq_analysis_sources_v1",
                "deal_id": deal_id,
                "sources": [{"source_id": f"SRC-{deal_id}", "status": "active"}],
            },
        )
    _write_json(
        bundle / "release/real_smoke.json",
        {
            "schema_version": "siq_ic_real_smoke_result_v1",
            "deal_id": deal_id,
            "run_id": run_id,
            "status": "passed",
            "execution_mode": "real",
            "hermes_called": True,
            "evidence_snapshot_hash": smoke_snapshot_hash or snapshot_hash,
        },
    )
    return bundle


def _insufficient_manifest(path: Path) -> Path:
    _write_json(
        path,
        {
            "schema_version": "siq_ic_golden_case_manifest_v1",
            "acceptance_status": "candidates_only",
            "quality_accepted": False,
            "cases": [
                {
                    "case_id": "GOLDEN-PMIC-INSUFFICIENT-EVIDENCE",
                    "scenario": "fail-closed insufficient Evidence",
                    "status": "candidate",
                    "quality_accepted": False,
                    "required_paths": [
                        "R0-block-or-degraded",
                        "claim-restriction",
                        "R4-insufficient-evidence",
                    ],
                    "known_gap": "Candidate still requires methodology approval.",
                }
            ],
        },
    )
    return path


def _append_real_task(
    bundle: Path,
    *,
    deal_id: str,
    snapshot_hash: str,
    phase: str,
    role: str,
) -> dict[str, object]:
    suffix = f"{phase}-{role}".replace(".", "_")
    task_id = f"ICTASK-{suffix}"
    run_id = f"run-{suffix}"
    raw_relative = f"audit/ic_agent_outputs/{task_id}/{run_id}.txt"
    raw_path = bundle / raw_relative
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps({"phase": phase, "role": role}) + "\n", encoding="utf-8")
    task = {
        "task_id": task_id,
        "agent_id": role,
        "phase": phase,
        "deal_id": deal_id,
        "status": "succeeded",
        "hermes_called": True,
        "prompt_contract_version": "siq_ic_phase_prompt_v5",
        "evidence_snapshot_hash": snapshot_hash,
        "workflow_run_id": "ICRUN-INSUFFICIENT-001",
        "hermes_run_id": run_id,
        "hermes_run_ids": [run_id],
        "contract_validation": {"passed": True},
        "methodology_refs": [{"ref_id": f"KBREF-{suffix}"}],
        "output_artifact_paths": [raw_relative],
        "output_artifact_hashes": {raw_relative: _sha256(raw_path)},
    }
    store_path = bundle / "phases/ic_agent_tasks.json"
    store = json.loads(store_path.read_text(encoding="utf-8")) if store_path.is_file() else {
        "schema_version": "siq_ic_agent_tasks_v1",
        "tasks": [],
    }
    store["tasks"].append(task)
    _write_json(store_path, store)
    return task


def _write_insufficient_input_identity(bundle: Path, *, deal_id: str) -> None:
    _write_json(
        bundle / "evidence/evidence_index.json",
        {"schema_version": "siq_deal_evidence_index_v1", "deal_id": deal_id, "items": []},
    )
    _write_json(
        bundle / "evidence/evidence_quality_report.json",
        {
            "schema_version": "siq_deal_evidence_quality_v1",
            "deal_id": deal_id,
            "critical_fact_status": "incomplete",
            "known_critical_fact_gaps": [
                "audited_financial_statements_missing",
                "freedom_to_operate_opinion_missing",
            ],
        },
    )


def test_default_manifest_paths_are_supported_without_an_invented_policy_contract():
    module = _load_module(EVALUATOR_PATH, "pmic_golden_evaluator_manifest_test")
    manifest = json.loads(module.DEFAULT_MANIFEST.read_text(encoding="utf-8"))

    required = {
        path
        for case in manifest["cases"]
        if case["case_id"] in module.INDEPENDENT_CASE_IDS
        for path in case["required_paths"]
    }

    assert required <= set(module.PATH_EVALUATORS)
    for case_id in module.INDEPENDENT_CASE_IDS:
        _, errors = module._manifest_case(module.DEFAULT_MANIFEST, case_id)
        assert errors == []


def test_evaluate_and_recompute_candidate_from_persisted_source_artifacts(tmp_path):
    module = _load_module(EVALUATOR_PATH, "pmic_golden_evaluator_happy_test")
    case_id = "GOLDEN-PMIC-ROLE-ROUTING"
    manifest = _manifest(tmp_path / "golden_manifest.json", {case_id})
    manifest_before = manifest.read_bytes()
    bundle = _bundle(
        tmp_path,
        deal_id="DEAL-GOLDEN-ROUTING-001",
        run_id="SMOKE-GOLDEN-ROUTING-001",
        snapshot_hash="1" * 64,
    )

    report = module.evaluate_case(bundle, case_id, manifest_path=manifest)

    assert report["passed"] is True
    assert report["result"]["status"] == "passed"
    assert report["result"]["quality_accepted"] is False
    assert report["result"]["errors"] == []
    path_payload = report["path_payloads"]["source-activation"]
    assert path_payload["status"] == "passed"
    assert all(source["exists"] for source in path_payload["source_artifacts"])
    assert all(len(source["sha256"]) == 64 for source in path_payload["source_artifacts"])
    assert manifest.read_bytes() == manifest_before

    recomputed = module.validate_candidate_result(bundle, manifest_path=manifest)
    assert recomputed["passed"] is True
    assert recomputed["errors"] == []


def test_missing_required_source_path_writes_only_a_failed_candidate(tmp_path):
    module = _load_module(EVALUATOR_PATH, "pmic_golden_evaluator_missing_test")
    case_id = "GOLDEN-PMIC-ROLE-ROUTING"
    manifest = _manifest(tmp_path / "golden_manifest.json", {case_id})
    bundle = _bundle(
        tmp_path,
        deal_id="DEAL-GOLDEN-MISSING-001",
        run_id="SMOKE-GOLDEN-MISSING-001",
        snapshot_hash="2" * 64,
        include_source_registry=False,
    )

    report = module.evaluate_case(bundle, case_id, manifest_path=manifest)

    assert report["passed"] is False
    assert report["result"]["status"] == "failed"
    assert report["result"]["quality_accepted"] is False
    assert "path_failed:source-activation" in report["errors"]
    path_payload = report["path_payloads"]["source-activation"]
    assert path_payload["status"] == "failed"
    registry = next(
        item for item in path_payload["source_artifacts"] if item["path"] == "sources/analysis_sources.json"
    )
    assert registry == {
        "path": "sources/analysis_sources.json",
        "exists": False,
        "sha256": None,
    }
    assert module.validate_candidate_result(bundle, manifest_path=manifest)["passed"] is False


def test_phase_artifact_must_bind_the_digest_verified_real_task(tmp_path):
    module = _load_module(EVALUATOR_PATH, "pmic_golden_evaluator_task_binding_test")
    case_id = "GOLDEN-PMIC-ROLE-ROUTING"
    manifest_path = tmp_path / "golden_manifest.json"
    _write_json(
        manifest_path,
        {
            "schema_version": "siq_ic_golden_case_manifest_v1",
            "acceptance_status": "candidates_only",
            "quality_accepted": False,
            "cases": [
                {
                    "case_id": case_id,
                    "scenario": "R0 task provenance",
                    "status": "candidate",
                    "quality_accepted": False,
                    "required_paths": ["R0"],
                    "known_gap": "Candidate fixture has not received methodology approval.",
                }
            ],
        },
    )
    deal_id = "DEAL-GOLDEN-TASK-001"
    snapshot_hash = "a" * 64
    bundle = _bundle(
        tmp_path,
        deal_id=deal_id,
        run_id="SMOKE-GOLDEN-TASK-001",
        snapshot_hash=snapshot_hash,
    )
    raw_relative = "audit/ic_agent_outputs/ICTASK-R0-001/run-real-001.txt"
    raw_path = bundle / raw_relative
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text('{"readiness":"ready"}\n', encoding="utf-8")
    _write_json(
        bundle / "phases/ic_agent_tasks.json",
        {
            "schema_version": "siq_ic_agent_tasks_v1",
            "tasks": [
                {
                    "task_id": "ICTASK-R0-001",
                    "agent_id": "siq_ic_master_coordinator",
                    "phase": "R0",
                    "deal_id": deal_id,
                    "status": "succeeded",
                    "hermes_called": True,
                    "prompt_contract_version": "siq_ic_phase_prompt_v5",
                    "evidence_snapshot_hash": snapshot_hash,
                    "workflow_run_id": "ICRUN-GOLDEN-001",
                    "hermes_run_id": "run-real-001",
                    "hermes_run_ids": ["run-real-001"],
                    "contract_validation": {"passed": True},
                    "methodology_refs": [{"ref_id": "KBREF-GOLDEN-001"}],
                    "output_artifact_paths": [raw_relative],
                    "output_artifact_hashes": {raw_relative: _sha256(raw_path)},
                }
            ],
        },
    )
    readiness_path = bundle / "phases/r0_readiness.json"
    readiness = {
        "schema_version": "siq_ic_r0_readiness_v1",
        "deal_id": deal_id,
        "evidence_snapshot_hash": snapshot_hash,
        "generation_mode": "model",
        "readiness": "ready",
        "blocking_reasons": [],
        "task_id": "ICTASK-R0-001",
        "hermes_run_id": "run-forged-999",
        "workflow_run_id": "ICRUN-GOLDEN-001",
    }
    _write_json(readiness_path, readiness)

    mismatched = module.evaluate_case(bundle, case_id, manifest_path=manifest_path)
    assert mismatched["passed"] is False
    task_binding = next(
        item for item in mismatched["path_payloads"]["R0"]["assertions"] if item["name"] == "R0.task_binding"
    )
    assert task_binding["passed"] is False

    readiness["hermes_run_id"] = "run-real-001"
    _write_json(readiness_path, readiness)
    bound = module.evaluate_case(bundle, case_id, manifest_path=manifest_path)
    assert bound["passed"] is True


def test_insufficient_case_accepts_audited_r0_terminal_without_synthesizing_r4(tmp_path):
    module = _load_module(EVALUATOR_PATH, "pmic_golden_evaluator_r0_terminal_test")
    case_id = "GOLDEN-PMIC-INSUFFICIENT-EVIDENCE"
    deal_id = "DEAL-GOLDEN-INSUFFICIENT-R0"
    snapshot_hash = "b" * 64
    manifest = _insufficient_manifest(tmp_path / "golden_manifest.json")
    bundle = _bundle(
        tmp_path,
        deal_id=deal_id,
        run_id="SMOKE-GOLDEN-INSUFFICIENT-R0",
        snapshot_hash=snapshot_hash,
    )
    _write_insufficient_input_identity(bundle, deal_id=deal_id)
    task = _append_real_task(
        bundle,
        deal_id=deal_id,
        snapshot_hash=snapshot_hash,
        phase="R0",
        role="siq_ic_master_coordinator",
    )
    _write_json(
        bundle / "phases/r0_readiness.json",
        {
            "schema_version": "siq_ic_r0_readiness_v1",
            "deal_id": deal_id,
            "evidence_snapshot_hash": snapshot_hash,
            "generation_mode": "model",
            "readiness": "needs_more_evidence",
            "evidence_gaps": ["audited_financial_statements_missing"],
            "blocking_reasons": ["critical_fact_incomplete"],
            "task_id": task["task_id"],
            "hermes_run_id": task["hermes_run_id"],
            "workflow_run_id": task["workflow_run_id"],
        },
    )
    _write_json(
        bundle / "phases/workflow_state.json",
        {
            "schema_version": "siq_deal_workflow_state_v1",
            "deal_id": deal_id,
            "status": "r0_blocked",
            "phases": {"R0": {"status": "blocked"}},
        },
    )
    smoke_path = bundle / "release/real_smoke.json"
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    smoke.update(
        {
            "status": "blocked",
            "workflow_blocked": True,
            "phase_runs": {
                "R0": {
                    "status": "blocked",
                    "task_validated": True,
                    "workflow_advanced": False,
                    "workflow_blocked": True,
                }
            },
        }
    )
    _write_json(smoke_path, smoke)

    result = module.evaluate_case(bundle, case_id, manifest_path=manifest)

    assert result["passed"] is True
    terminal = result["path_payloads"]["R4-insufficient-evidence"]
    assert terminal["status"] == "passed"
    assert any(
        row["name"] == "R4.absent_after_early_terminal" and row["passed"] is True
        for row in terminal["assertions"]
    )
    assert not (bundle / "phases/r4_decision.json").exists()

    _write_json(
        bundle / "decision/report_quality.json",
        {"schema_version": "siq_ic_report_quality_v1", "status": "pass"},
    )
    _write_json(
        bundle / "decision/factcheck_task.json",
        {"schema_version": "siq_ic_factcheck_task_v1", "phase": "R4", "status": "succeeded"},
    )
    final_report = bundle / "decision/IC_DECISION_REPORT.md"
    final_report.parent.mkdir(parents=True, exist_ok=True)
    final_report.write_text("# stale downstream decision\n", encoding="utf-8")
    _write_json(
        bundle / "factcheck/factcheck.json",
        {"schema_version": "siq_ic_report_factcheck_v1", "status": "pass"},
    )
    downstream_discussion = bundle / "discussion/04_R3_红蓝对抗.md"
    downstream_discussion.parent.mkdir(parents=True, exist_ok=True)
    downstream_discussion.write_text("# stale R3 output\n", encoding="utf-8")
    _write_json(
        bundle / "phases/ic_agent_handoffs.json",
        {
            "schema_version": "siq_ic_agent_handoffs_v1",
            "handoffs": [{"handoff_id": "ICHANDOFF-R4-STALE", "phase": "R4"}],
            "payloads": {"ICHANDOFF-R4-STALE": {"payload": {}}},
        },
    )
    downstream_task = _append_real_task(
        bundle,
        deal_id=deal_id,
        snapshot_hash=snapshot_hash,
        phase="R4",
        role="siq_ic_chairman",
    )
    _write_json(
        bundle / "phases/ic_task_leases.json",
        {
            "schema_version": "siq_ic_task_leases_v1",
            "claims": [{"task_key": "ICRUN-STALE:ICFACT-STALE:deadbeef", "status": "succeeded"}],
        },
    )

    contaminated = module.evaluate_case(bundle, case_id, manifest_path=manifest, write=False)

    assert contaminated["passed"] is False
    claim_assertions = {
        row["name"]: row
        for row in contaminated["path_payloads"]["claim-restriction"]["assertions"]
    }
    unexpected_artifacts = claim_assertions["claim_restriction.no_illegal_downstream_artifacts"]
    assert unexpected_artifacts["passed"] is False
    assert {
        "decision/report_quality.json",
        "decision/factcheck_task.json",
        "decision/IC_DECISION_REPORT.md",
        "factcheck/factcheck.json",
        "discussion/04_R3_红蓝对抗.md",
        "phases/ic_agent_handoffs.json#ICHANDOFF-R4-STALE",
    } <= set(unexpected_artifacts["actual"])
    unexpected_tasks = claim_assertions["claim_restriction.no_illegal_downstream_tasks"]
    assert unexpected_tasks["passed"] is False
    assert downstream_task["task_id"] in unexpected_tasks["actual"]
    assert "lease:ICRUN-STALE:ICFACT-STALE:deadbeef" in unexpected_tasks["actual"]


def test_insufficient_case_rejects_fallback_r4_after_audited_r0_terminal(tmp_path):
    module = _load_module(EVALUATOR_PATH, "pmic_golden_evaluator_no_fallback_r4_test")
    case_id = "GOLDEN-PMIC-INSUFFICIENT-EVIDENCE"
    deal_id = "DEAL-GOLDEN-INSUFFICIENT-FALLBACK"
    snapshot_hash = "c" * 64
    manifest = _insufficient_manifest(tmp_path / "golden_manifest.json")
    bundle = _bundle(
        tmp_path,
        deal_id=deal_id,
        run_id="SMOKE-GOLDEN-INSUFFICIENT-FALLBACK",
        snapshot_hash=snapshot_hash,
    )
    _write_insufficient_input_identity(bundle, deal_id=deal_id)
    task = _append_real_task(
        bundle,
        deal_id=deal_id,
        snapshot_hash=snapshot_hash,
        phase="R0",
        role="siq_ic_master_coordinator",
    )
    _write_json(
        bundle / "phases/r0_readiness.json",
        {
            "schema_version": "siq_ic_r0_readiness_v1",
            "deal_id": deal_id,
            "evidence_snapshot_hash": snapshot_hash,
            "generation_mode": "model",
            "readiness": "blocked",
            "evidence_gaps": ["freedom_to_operate_opinion_missing"],
            "blocking_reasons": ["critical_fact_incomplete"],
            "task_id": task["task_id"],
            "hermes_run_id": task["hermes_run_id"],
            "workflow_run_id": task["workflow_run_id"],
        },
    )
    _write_json(
        bundle / "phases/workflow_state.json",
        {
            "schema_version": "siq_deal_workflow_state_v1",
            "deal_id": deal_id,
            "status": "r0_blocked",
            "phases": {"R0": {"status": "blocked"}},
        },
    )
    smoke_path = bundle / "release/real_smoke.json"
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    smoke.update(
        {
            "status": "blocked",
            "phase_runs": {
                "R0": {"status": "blocked", "task_validated": True, "workflow_advanced": False}
            },
        }
    )
    _write_json(smoke_path, smoke)
    _write_json(
        bundle / "phases/r4_decision.json",
        {
            "schema_version": "siq_ic_r4_decision_v2",
            "deal_id": deal_id,
            "evidence_snapshot_hash": snapshot_hash,
            "generation_mode": "deterministic_fallback",
            "recommendation": "insufficient_evidence",
            "decision": "insufficient_evidence",
        },
    )

    result = module.evaluate_case(bundle, case_id, manifest_path=manifest)

    assert result["passed"] is False
    assert "path_failed:R4-insufficient-evidence" in result["errors"]
    assertions = result["path_payloads"]["R4-insufficient-evidence"]["assertions"]
    assert any(row["name"] == "R2.current_model_reports" and row["passed"] is False for row in assertions)
    assert any(row["name"] == "R3.real_model" and row["passed"] is False for row in assertions)
    assert any(row["name"] == "R4.current_model_decision" and row["passed"] is False for row in assertions)


def test_insufficient_case_accepts_audited_r1_5_return_to_evidence_loop(tmp_path):
    module = _load_module(EVALUATOR_PATH, "pmic_golden_evaluator_r15_terminal_test")
    case_id = "GOLDEN-PMIC-INSUFFICIENT-EVIDENCE"
    deal_id = "DEAL-GOLDEN-INSUFFICIENT-R15"
    snapshot_hash = "d" * 64
    manifest = _insufficient_manifest(tmp_path / "golden_manifest.json")
    bundle = _bundle(
        tmp_path,
        deal_id=deal_id,
        run_id="SMOKE-GOLDEN-INSUFFICIENT-R15",
        snapshot_hash=snapshot_hash,
    )
    _write_insufficient_input_identity(bundle, deal_id=deal_id)
    r0_task = _append_real_task(
        bundle,
        deal_id=deal_id,
        snapshot_hash=snapshot_hash,
        phase="R0",
        role="siq_ic_master_coordinator",
    )
    _write_json(
        bundle / "phases/r0_readiness.json",
        {
            "schema_version": "siq_ic_r0_readiness_v1",
            "deal_id": deal_id,
            "evidence_snapshot_hash": snapshot_hash,
            "generation_mode": "model",
            "readiness": "ready",
            "evidence_gaps": [
                "audited_financial_statements_missing",
                "freedom_to_operate_opinion_missing",
            ],
            "blocking_reasons": [],
            "task_id": r0_task["task_id"],
            "hermes_run_id": r0_task["hermes_run_id"],
            "workflow_run_id": r0_task["workflow_run_id"],
        },
    )
    reports: dict[str, object] = {}
    for phase, roles in (("R1A", module.R1A_ROLES), ("R1B", module.R1B_ROLES)):
        for role in sorted(roles):
            task = _append_real_task(
                bundle,
                deal_id=deal_id,
                snapshot_hash=snapshot_hash,
                phase=phase,
                role=role,
            )
            reports[role] = {
                "schema_version": "siq_ic_expert_report_v2",
                "deal_id": deal_id,
                "agent_id": role,
                "phase": phase,
                "evidence_snapshot_hash": snapshot_hash,
                "generation_mode": "model",
                "task_id": task["task_id"],
                "hermes_run_id": task["hermes_run_id"],
                "workflow_run_id": task["workflow_run_id"],
                "methodology_refs": task["methodology_refs"],
                "recommendation": "insufficient_evidence",
                "claims": [
                    {
                        "claim_id": f"CLM-MISSING-{phase}-{role}",
                        "status": "missing",
                        "evidence_ids": [],
                    }
                ],
            }
    _write_json(bundle / "phases/r1_reports.json", reports)
    r1_5_task = _append_real_task(
        bundle,
        deal_id=deal_id,
        snapshot_hash=snapshot_hash,
        phase="R1.5",
        role="siq_ic_chairman",
    )
    _write_json(
        bundle / "phases/r1_5_disputes.json",
        {
            "schema_version": "siq_ic_disputes_v1",
            "deal_id": deal_id,
            "disputes": [
                {
                    "dispute_id": "DSP-MISSING-FTO",
                    "severity": "critical",
                    "status": "needs_more_evidence",
                    "resolved": False,
                    "evidence_snapshot_hash": snapshot_hash,
                    "chairman_ruling": {
                        "schema_version": "siq_deal_r1_5_dispute_ruling_v1",
                        "deal_id": deal_id,
                        "dispute_id": "DSP-MISSING-FTO",
                        "agent_id": "siq_ic_chairman",
                        "decision": "needs_more_evidence",
                        "ruling": "needs_more_evidence",
                        "resolved": False,
                        "required_followups": ["obtain_external_freedom_to_operate_opinion"],
                        "generation_mode": "model",
                        "task_id": r1_5_task["task_id"],
                        "workflow_run_id": r1_5_task["workflow_run_id"],
                        "hermes_run_id": r1_5_task["hermes_run_id"],
                        "evidence_snapshot_hash": snapshot_hash,
                    },
                }
            ],
        },
    )
    _write_json(
        bundle / "phases/workflow_state.json",
        {
            "schema_version": "siq_deal_workflow_state_v1",
            "deal_id": deal_id,
            "status": "r1_5_blocked",
            "phases": {
                "R0": {"status": "completed"},
                "R1": {"status": "completed"},
                "R1.5": {"status": "blocked"},
            },
        },
    )
    smoke_path = bundle / "release/real_smoke.json"
    smoke = json.loads(smoke_path.read_text(encoding="utf-8"))
    smoke.update(
        {
            "status": "blocked",
            "workflow_blocked": True,
            "phase_runs": {
                "R0": {"status": "passed", "task_validated": True, "workflow_advanced": True},
                "R1": {"status": "passed", "task_validated": True, "workflow_advanced": True},
                "R1.5": {
                    "status": "blocked",
                    "result_status": "needs_more_evidence",
                    "task_validated": True,
                    "workflow_advanced": False,
                    "workflow_blocked": True,
                },
            },
        }
    )
    _write_json(smoke_path, smoke)

    result = module.evaluate_case(bundle, case_id, manifest_path=manifest)

    assert result["passed"] is True
    assert result["path_payloads"]["R0-block-or-degraded"]["status"] == "passed"
    assert result["path_payloads"]["claim-restriction"]["status"] == "passed"
    assert result["path_payloads"]["R4-insufficient-evidence"]["status"] == "passed"
    assert not (bundle / "phases/r2_reports.json").exists()
    assert not (bundle / "phases/r3_reports.json").exists()
    assert not (bundle / "phases/r4_decision.json").exists()

    disputes_path = bundle / "phases/r1_5_disputes.json"
    disputes = json.loads(disputes_path.read_text(encoding="utf-8"))
    del disputes["disputes"][0]["chairman_ruling"]["task_id"]
    _write_json(disputes_path, disputes)

    forged = module.evaluate_case(bundle, case_id, manifest_path=manifest, write=False)

    assert forged["passed"] is False
    claim_assertions = {
        row["name"]: row
        for row in forged["path_payloads"]["claim-restriction"]["assertions"]
    }
    lineage = claim_assertions["claim_restriction.r1_5_terminal_lineage"]
    assert lineage["passed"] is False
    assert "DSP-MISSING-FTO:ruling_task_id_mismatch" in lineage["actual"]


def test_recompute_rejects_deleted_path_artifact_and_changed_source(tmp_path):
    module = _load_module(EVALUATOR_PATH, "pmic_golden_evaluator_tamper_test")
    case_id = "GOLDEN-PMIC-ROLE-ROUTING"
    manifest = _manifest(tmp_path / "golden_manifest.json", {case_id})
    bundle = _bundle(
        tmp_path,
        deal_id="DEAL-GOLDEN-TAMPER-001",
        run_id="SMOKE-GOLDEN-TAMPER-001",
        snapshot_hash="3" * 64,
    )
    assert module.evaluate_case(bundle, case_id, manifest_path=manifest)["passed"] is True

    registry = bundle / "sources/analysis_sources.json"
    _write_json(registry, {"schema_version": "siq_analysis_sources_v1", "sources": []})
    changed = module.validate_candidate_result(bundle, manifest_path=manifest)
    assert changed["passed"] is False
    assert any(error.startswith("path_recompute_mismatch:source-activation") for error in changed["errors"])

    artifact = bundle / "evaluation/golden/source-activation.json"
    artifact.unlink()
    deleted = module.validate_candidate_result(bundle, manifest_path=manifest)
    assert deleted["passed"] is False
    assert "path_artifact_invalid:source-activation" in deleted["errors"]


def test_validate_rejects_external_result_and_invalid_evaluation_timestamp(tmp_path):
    module = _load_module(EVALUATOR_PATH, "pmic_golden_evaluator_result_test")
    case_id = "GOLDEN-PMIC-ROLE-ROUTING"
    manifest = _manifest(tmp_path / "golden_manifest.json", {case_id})
    bundle = _bundle(
        tmp_path,
        deal_id="DEAL-GOLDEN-RESULT-001",
        run_id="SMOKE-GOLDEN-RESULT-001",
        snapshot_hash="4" * 64,
    )
    assert module.evaluate_case(bundle, case_id, manifest_path=manifest)["passed"] is True
    result_path = bundle / module.RESULT_PATH

    payload = json.loads(result_path.read_text(encoding="utf-8"))
    payload["evaluated_at"] = "not-a-timestamp"
    _write_json(result_path, payload)
    invalid_time = module.validate_candidate_result(bundle, manifest_path=manifest)
    assert invalid_time["passed"] is False
    assert "result_evaluated_at_invalid" in invalid_time["errors"]

    outside = tmp_path / "outside-result.json"
    _write_json(outside, payload)
    external = module.validate_candidate_result(bundle, manifest_path=manifest, result_path=outside)
    assert external["passed"] is False
    assert "result_path_outside_bundle" in external["errors"]


def test_evaluate_does_not_create_a_nonexistent_bundle(tmp_path):
    module = _load_module(EVALUATOR_PATH, "pmic_golden_evaluator_absent_bundle_test")
    case_id = "GOLDEN-PMIC-ROLE-ROUTING"
    manifest = _manifest(tmp_path / "golden_manifest.json", {case_id})
    missing = tmp_path / "DEAL-DOES-NOT-EXIST"

    report = module.evaluate_case(missing, case_id, manifest_path=manifest)

    assert report["passed"] is False
    assert "bundle_not_directory" in report["errors"]
    assert not missing.exists()


def _evaluate_independent_suite(tmp_path: Path, module, *, duplicate_run: bool = False):
    manifest = _manifest(tmp_path / "golden_manifest.json", CASE_IDS)
    bundles = []
    for index, case_id in enumerate(sorted(CASE_IDS), start=1):
        run_index = 1 if duplicate_run and index == 2 else index
        bundle = _bundle(
            tmp_path,
            deal_id=f"DEAL-GOLDEN-{index:03d}",
            run_id=f"SMOKE-GOLDEN-{run_index:03d}",
            snapshot_hash=f"{index:x}" * 64,
            smoke_snapshot_hash=("f" * 64 if case_id == "GOLDEN-PMIC-SNAPSHOT-STALE" else None),
        )
        result = module.evaluate_case(bundle, case_id, manifest_path=manifest)
        assert result["passed"] is True
        bundles.append(bundle)
    release_bundle = tmp_path / "DEAL-RELEASE-001"
    release_bundle.mkdir()
    return manifest, release_bundle, bundles


def test_bindings_require_five_independent_deal_run_result_and_digest_identities(tmp_path):
    module = _load_module(EVALUATOR_PATH, "pmic_golden_evaluator_bind_test")
    gate = _load_module(RELEASE_GATE_PATH, "pmic_release_gate_binding_compat_test")
    manifest, release_bundle, bundles = _evaluate_independent_suite(tmp_path, module)
    manifest_before = manifest.read_bytes()

    report = module.build_bindings(
        release_bundle,
        bundles,
        suite_id="GOLDEN-SUITE-OFFLINE-001",
        manifest_path=manifest,
    )

    assert report["passed"] is True
    payload = report["bindings"]
    assert payload["status"] == "passed"
    assert payload["quality_accepted"] is False
    assert len(payload["bindings"]) == 5
    for field in ("deal_id", "run_id", "result_id", "result_sha256", "bundle_path"):
        assert len({item[field] for item in payload["bindings"]}) == 5
    for item in payload["bindings"]:
        result_path = tmp_path / item["bundle_path"] / item["result_path"]
        assert item["result_sha256"] == _sha256(result_path)
    assert manifest.read_bytes() == manifest_before

    manifest_validation = {
        "coverage": {
            case_id: {"case_id": case_id, "required_paths": ["source-activation"]}
            for case_id in CASE_IDS
        }
    }
    metric = gate._golden_case_binding_metric(release_bundle, payload, manifest_validation)
    assert metric["passed"] is True
    assert metric["distinct_run_count"] == 5
    assert metric["distinct_deal_count"] == 5

    unsafe_output = release_bundle / "manifest.json"
    refused = module.build_bindings(
        release_bundle,
        bundles,
        suite_id="GOLDEN-SUITE-OFFLINE-UNSAFE-OUTPUT",
        manifest_path=manifest,
        output_path=unsafe_output,
    )
    assert refused["passed"] is False
    assert "bindings_output_must_be_canonical" in refused["errors"]
    assert not unsafe_output.exists()


def test_bindings_fail_closed_for_reused_run_or_missing_candidate(tmp_path):
    module = _load_module(EVALUATOR_PATH, "pmic_golden_evaluator_bind_failure_test")
    manifest, release_bundle, bundles = _evaluate_independent_suite(tmp_path, module, duplicate_run=True)

    reused = module.build_bindings(
        release_bundle,
        bundles,
        suite_id="GOLDEN-SUITE-OFFLINE-REUSED",
        manifest_path=manifest,
    )
    assert reused["passed"] is False
    assert reused["bindings"]["status"] == "failed"
    assert "run_id_not_independent" in reused["errors"]
    assert reused["bindings"]["quality_accepted"] is False

    missing = module.build_bindings(
        release_bundle,
        bundles[:-1],
        suite_id="GOLDEN-SUITE-OFFLINE-MISSING",
        manifest_path=manifest,
    )
    assert missing["passed"] is False
    assert missing["bindings"]["status"] == "failed"
    assert any(error.startswith("required_case_missing:") for error in missing["errors"])
