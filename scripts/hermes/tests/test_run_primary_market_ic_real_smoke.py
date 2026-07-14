from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = PROJECT_ROOT / "scripts" / "hermes" / "run_primary_market_ic_real_smoke.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("primary_market_ic_real_smoke_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_release_gate():
    source = PROJECT_ROOT / "scripts" / "maintenance" / "run_primary_market_ic_release_gate.py"
    spec = importlib.util.spec_from_file_location("primary_market_ic_release_gate_for_smoke_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> Path:
    package = tmp_path / "source" / "DEAL-REAL-SMOKE-001"
    _write_json(package / "manifest.json", {"schema_version": "siq_deal_manifest_v1", "deal_id": package.name})
    _write_json(package / "project_meta.json", {"schema_version": "siq_deal_project_v1", "deal_id": package.name})
    _write_json(
        package / "phases/workflow_state.json",
        {"schema_version": "siq_deal_workflow_state_v1", "deal_id": package.name, "current_phase": "R0"},
    )
    _write_json(
        package / "evidence/evidence_snapshot.json",
        {
            "deal_id": package.name,
            "snapshot_hash": "a" * 64,
            "active_sources": [{"source_id": "SRC-SMOKE-001"}],
        },
    )
    (package / "evidence/evidence_items.ndjson").write_text(
        json.dumps({"evidence_id": "EVID-REAL-SMOKE-001", "source_class": "project_evidence"}) + "\n",
        encoding="utf-8",
    )
    return package


def _receipt(module, profile_id: str, phase: str) -> dict:
    private = profile_id.removeprefix("siq_")
    return {
        "schema_version": "siq_ic_startup_receipt_v2",
        "receipt_id": f"RECEIPT-{profile_id}-{phase}",
        "deal_id": "DEAL-REAL-SMOKE-001",
        "agent_id": profile_id,
        "phase": phase,
        "round_name": phase,
        "retrieval_status": "ready",
        "readiness_status": "current",
        "milvus_used": True,
        "shared_collection": "siq_deal_shared",
        "private_collection": profile_id,
        "physical_collections": {"siq_deal_shared": "ic_collaboration_shared", profile_id: private},
        "project_evidence_hits": [
            {"evidence_id": "EVID-REAL-SMOKE-001", "source_class": "project_evidence", "text": "secret project text"}
        ],
        "background_knowledge_hits": [
            {"source_id": f"ICKB-{profile_id}", "collection": private, "source_class": "background_knowledge", "text": "secret KB text"}
        ],
        "background_knowledge_refs": [
            {"ref_id": f"KBREF-{profile_id}", "collection": private, "source_class": "background_knowledge", "title": "secret title"}
        ],
        "vector_retrieval": {
            "status": "completed",
            "milvus_used": True,
            "collections": ["siq_deal_shared", profile_id],
            "physical_collections": {"siq_deal_shared": "ic_collaboration_shared", profile_id: private},
        },
        "source_ids": ["SRC-SMOKE-001"],
        "evidence_snapshot_hash": "a" * 64,
        "gate": {"allowed_to_speak": True, "blocking_reasons": []},
    }


def _task(profile_id: str, phase: str, *, round_name: str | None = None) -> dict:
    effective_round = round_name or phase
    output_schema = (
        "siq_ic_r0_readiness_v1"
        if phase == "R0"
        else "siq_ic_r1_5_chairman_rulings_v2"
        if phase == "R1.5"
        else "siq_ic_expert_report_v2"
    )
    artifact_schema = None if phase == "R1.5" else output_schema
    run_id = f"RUN-{profile_id}-{phase}"
    runtime = {
        "schema_version": "hermes.run_runtime.v1",
        "requested_model": profile_id,
        "configured": {"provider": "fixture-provider", "model": "fixture-model-v1"},
        "effective": {"provider": "fixture-provider", "model": "fixture-model-v1"},
        "fallback": {"activated": False},
    }
    prompt_sha256 = hashlib.sha256(f"prompt:{run_id}".encode()).hexdigest()
    return {
        "schema_version": "siq_ic_agent_task_v2",
        "task_id": f"ICTASK-{profile_id}-{phase}",
        "agent_id": profile_id,
        "phase": phase,
        "round_name": effective_round,
        "status": "succeeded",
        "hermes_called": True,
        "hermes_run_id": run_id,
        "hermes_run_ids": [run_id],
        "output_schema": output_schema,
        "input_digest": "b" * 64,
        "handoff_digest": "c" * 64,
        "evidence_snapshot_hash": "a" * 64,
        "startup_retrieval_gate": {"receipt_id": f"RECEIPT-{profile_id}-{phase}"},
        "contract_validation": {
            "passed": True,
            "validated_by": "ic_phase_orchestrator",
            "output_schema": output_schema,
            "artifact_schema": artifact_schema,
        },
        "model_execution_audit": {
            "schema_version": "siq_ic_model_execution_audit_v1",
            "runtime_metadata_status": "verified",
            "attempt_count": 1,
            "attempts": [
                {
                    "hermes_run_id": run_id,
                    "purpose": "generation",
                    "prompt_sha256": prompt_sha256,
                    "terminal_status": "succeeded",
                    "runtime_metadata_status": "verified",
                    "runtime": runtime,
                }
            ],
            "final_hermes_run_id": run_id,
            "final_prompt_sha256": prompt_sha256,
            "final_runtime": runtime,
        },
        "validated_output": (
            {"rulings": []} if phase == "R1.5" else {"schema_version": artifact_schema}
        ),
        "prompt": "must never be copied to release report",
    }


def test_committed_fixture_has_formal_evidence_identity_and_preflight_gate(tmp_path):
    module = _load_module()
    fixture = (
        PROJECT_ROOT
        / "eval_datasets"
        / "primary_market_ic_real_smoke"
        / "DEAL-PMIC-REAL-SMOKE-2026"
    )
    source, deal_id = module._fixture_identity(fixture)
    assert deal_id == "DEAL-PMIC-REAL-SMOKE-2026"

    snapshot = json.loads((source / "evidence/evidence_snapshot.json").read_text(encoding="utf-8"))
    index_path = source / "evidence/evidence_index.json"
    index_hash = hashlib.sha256(index_path.read_bytes()).hexdigest()
    assert snapshot["evidence_index_sha256"] == index_hash
    assert snapshot["source_ids"] == [snapshot["active_sources"][0]["source_id"]]
    active = snapshot["active_sources"][0]
    digest_input = "\n".join(
        [
            "siq_deal_evidence_item_v1",
            f"{active['source_id']}:{active['archive_manifest_sha256']}",
            f"evidence_index:{index_hash}",
        ]
    ).encode("utf-8")
    assert snapshot["snapshot_hash"] == hashlib.sha256(digest_input).hexdigest()

    evidence = [
        json.loads(line)
        for line in (source / "evidence/evidence_items.ndjson").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    required = {
        "evidence_id",
        "deal_id",
        "document_id",
        "source_id",
        "source_type",
        "source_path",
        "quote",
        "evidence_type",
        "dimension",
        "locator",
        "source_anchor",
    }
    assert len(evidence) == 16
    assert {item["dimension"] for item in evidence} == {"business", "finance", "legal", "risk"}
    assert all(required <= item.keys() for item in evidence)
    assert all(item["source_id"] == active["source_id"] for item in evidence)

    wiki_root = tmp_path / "wiki"
    package = wiki_root / "deals" / deal_id
    package.parent.mkdir(parents=True)
    shutil.copytree(source, package)
    preflight = module.deal_contracts.run_deal_preflight(deal_id, wiki_root=wiki_root)
    assert not [item for item in preflight["checks"] if item["status"] == "fail"]
    checks = {item["id"]: item for item in preflight["checks"]}
    assert checks["identity.deal_id"]["status"] == "pass"
    assert checks["retrieval.evidence_snapshot"]["status"] == "pass"
    assert checks["evidence.gate"]["status"] == "pass"


def _r1_phase(profile_id: str) -> str:
    return "R1B" if profile_id in {"siq_ic_risk_controller", "siq_ic_chairman"} else "R1A"


def test_default_mode_is_dry_run_and_never_calls_retrieval_or_hermes(monkeypatch, tmp_path, capsys):
    module = _load_module()
    fixture = _fixture(tmp_path)
    run_root = tmp_path / "dry-run"
    monkeypatch.setattr(
        module.ic_startup_retrieval,
        "generate_startup_retrieval_receipt",
        lambda *args, **kwargs: pytest.fail("dry-run called startup retrieval"),
    )
    monkeypatch.setattr(
        module.ic_agent_runtime,
        "run_workflow_r0_model",
        lambda *args, **kwargs: pytest.fail("dry-run called Hermes workflow"),
    )

    exit_code = module.main(["--fixture", str(fixture), "--run-root", str(run_root)])

    assert exit_code == 0
    payload = json.loads((run_root / "release/real_smoke.json").read_text(encoding="utf-8"))
    assert payload["execution_mode"] == "dry_run"
    assert payload["status"] == "dry_run"
    assert payload["hermes_called"] is False
    assert payload["contract_validation"]["passed"] is False
    assert "report_path" in capsys.readouterr().out
    assert not (run_root / "wiki").exists()


def test_real_r0_r1_smoke_covers_seven_profiles_and_writes_release_contract(monkeypatch, tmp_path):
    module = _load_module()
    fixture = _fixture(tmp_path)
    run_root = tmp_path / "real-run"
    calls: list[tuple[str, str]] = []

    def fake_receipt(deal_id, profile_id, *, round_name, **kwargs):
        calls.append((profile_id, round_name))
        return _receipt(module, profile_id, round_name)

    monkeypatch.setattr(module.ic_startup_retrieval, "generate_startup_retrieval_receipt", fake_receipt)
    monkeypatch.setattr(
        module.ic_startup_retrieval,
        "current_evidence_identity",
        lambda *args, **kwargs: {"evidence_snapshot_hash": "a" * 64, "source_ids": ["SRC-SMOKE-001"]},
    )

    async def fake_r0(*args, **kwargs):
        profile_id = module.ic_phase_orchestrator.COORDINATOR_AGENT_ID
        return {"status": "completed", "hermes_called": True, "workflow_advanced": True, "task": _task(profile_id, "R0")}

    async def fake_r1(deal_id, profile_id, **kwargs):
        return {
            "status": "completed",
            "hermes_called": True,
            "report_written": True,
            "workflow_advanced": True,
            "phase_task_envelope": _task(profile_id, _r1_phase(profile_id), round_name="R1"),
        }

    monkeypatch.setattr(module.ic_agent_runtime, "run_workflow_r0_model", fake_r0)
    monkeypatch.setattr(module.ic_agent_runtime, "run_workflow_r1_agent", fake_r1)
    monkeypatch.setattr(
        module.hermes_client,
        "hermes_profile_config",
        lambda profile: {"base": "http://user:secret@127.0.0.1:1234/v1/runs?token=secret", "model": profile},
    )

    report, report_path = asyncio.run(
        module.run_real(
            fixture=fixture,
            run_root=run_root,
            phases=["R0", "R1"],
            profiles=list(module.ic_policy.IC_PROFILE_IDS),
            resume=False,
            timeout=30,
            retrieval_limit=5,
        )
    )

    assert report_path == run_root / "wiki/deals/DEAL-REAL-SMOKE-001/release/real_smoke.json"
    assert report["schema_version"] == "siq_ic_real_smoke_result_v1"
    assert report["execution_mode"] == "real"
    assert report["status"] == "passed"
    assert report["hermes_called"] is True
    assert set(report["agent_retrievals"]) == set(module.ic_policy.IC_PROFILE_IDS)
    assert all(item["task_count"] >= 1 for item in report["profile_results"].values())
    assert report["contract_validation"] == {
        "passed": True,
        "required_profile_count": 7,
        "validated_profile_count": 7,
        "errors": [],
    }
    assert len(calls) == 7
    preflight = json.loads(
        (run_root / "wiki/deals/DEAL-REAL-SMOKE-001/phases/preflight.json").read_text(encoding="utf-8")
    )
    assert preflight["deal_id"] == "DEAL-REAL-SMOKE-001"
    assert preflight["checks"]
    assert {
        task["phase"]
        for result in report["profile_results"].values()
        for task in result["tasks"]
        if task["round_name"] == "R1"
    } == {"R1A", "R1B"}
    assert (run_root / "wiki/deals/DEAL-REAL-SMOKE-001").is_dir()
    assert fixture.is_dir()
    serialized = json.dumps(report, ensure_ascii=False)
    assert "secret project text" not in serialized
    assert "secret KB text" not in serialized
    assert "secret title" not in serialized
    assert "must never be copied" not in serialized
    assert "user:secret" not in serialized
    assert "token=secret" not in serialized
    gate = _load_release_gate()
    matrix = json.loads(gate.DEFAULT_PROFILE_MATRIX.read_text(encoding="utf-8"))
    gate_metric = gate._real_smoke_metric(
        report,
        gate._profile_collections(matrix),
        expected_deal_id="DEAL-REAL-SMOKE-001",
        expected_snapshot_hash="a" * 64,
    )
    assert gate_metric["passed"] is False
    assert gate_metric["routing"]["passed"] is True
    assert gate_metric["errors"] == [
        "phase_run_missing:R1.5",
        "phase_run_missing:R2",
        "phase_run_missing:R3",
        "phase_run_missing:R4",
    ]


def test_private_kb_failure_is_fail_closed_and_does_not_call_hermes(monkeypatch, tmp_path):
    module = _load_module()
    fixture = _fixture(tmp_path)
    called = False

    def blocked_receipt(deal_id, profile_id, *, round_name, **kwargs):
        payload = _receipt(module, profile_id, round_name)
        payload["background_knowledge_hits"] = []
        payload["retrieval_status"] = "blocked"
        payload["gate"] = {"allowed_to_speak": False, "blocking_reasons": ["private_kb_empty"]}
        return payload

    async def forbidden_r0(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("Hermes must not run after private KB failure")

    monkeypatch.setattr(module.ic_startup_retrieval, "generate_startup_retrieval_receipt", blocked_receipt)
    monkeypatch.setattr(
        module.ic_startup_retrieval,
        "current_evidence_identity",
        lambda *args, **kwargs: {"evidence_snapshot_hash": "a" * 64},
    )
    monkeypatch.setattr(module.ic_agent_runtime, "run_workflow_r0_model", forbidden_r0)

    report, report_path = asyncio.run(
        module.run_real(
            fixture=fixture,
            run_root=tmp_path / "blocked-run",
            phases=["R0"],
            profiles=[module.ic_phase_orchestrator.COORDINATOR_AGENT_ID],
            resume=False,
            timeout=30,
            retrieval_limit=5,
        )
    )

    assert called is False
    assert report["status"] == "failed"
    assert report["hermes_called"] is False
    assert report["contract_validation"]["passed"] is False
    assert report["errors"][0]["phase"] == "R0"
    assert report["errors"][0]["error_type"] == "ValueError"
    assert report["errors"][0]["message"].startswith("startup retrieval blocked for siq_ic_master_coordinator")
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == "failed"


def test_resume_keeps_completed_profiles_and_clears_retried_phase_error(monkeypatch, tmp_path):
    module = _load_module()
    fixture = _fixture(tmp_path)
    run_root = tmp_path / "resume-run"
    failed_once = False
    r1_calls: list[str] = []

    monkeypatch.setattr(
        module.ic_startup_retrieval,
        "generate_startup_retrieval_receipt",
        lambda deal_id, profile_id, *, round_name, **kwargs: _receipt(module, profile_id, round_name),
    )
    monkeypatch.setattr(
        module.ic_startup_retrieval,
        "current_evidence_identity",
        lambda *args, **kwargs: {"evidence_snapshot_hash": "a" * 64, "source_ids": ["SRC-SMOKE-001"]},
    )

    async def fake_r0(*args, **kwargs):
        profile_id = module.ic_phase_orchestrator.COORDINATOR_AGENT_ID
        return {"status": "completed", "hermes_called": True, "workflow_advanced": True, "task": _task(profile_id, "R0")}

    async def flaky_r1(deal_id, profile_id, **kwargs):
        nonlocal failed_once
        r1_calls.append(profile_id)
        if profile_id == "siq_ic_sector_expert" and not failed_once:
            failed_once = True
            raise RuntimeError("intentional test failure")
        return {
            "status": "completed",
            "hermes_called": True,
            "report_written": True,
            "workflow_advanced": True,
            "phase_task_envelope": _task(profile_id, _r1_phase(profile_id), round_name="R1"),
        }

    monkeypatch.setattr(module.ic_agent_runtime, "run_workflow_r0_model", fake_r0)
    monkeypatch.setattr(module.ic_agent_runtime, "run_workflow_r1_agent", flaky_r1)

    first, _ = asyncio.run(
        module.run_real(
            fixture=fixture,
            run_root=run_root,
            phases=["R0", "R1"],
            profiles=list(module.ic_policy.IC_PROFILE_IDS),
            resume=False,
            timeout=30,
            retrieval_limit=5,
        )
    )
    resumed, _ = asyncio.run(
        module.run_real(
            fixture=fixture,
            run_root=run_root,
            phases=["R0", "R1"],
            profiles=list(module.ic_policy.IC_PROFILE_IDS),
            resume=True,
            timeout=30,
            retrieval_limit=5,
        )
    )

    assert first["status"] == "failed"
    assert resumed["status"] == "passed"
    assert resumed["errors"] == []
    assert r1_calls.count("siq_ic_strategist") == 1
    assert r1_calls.count("siq_ic_sector_expert") == 2
    assert resumed["phase_runs"]["R0"]["status"] == "passed"
    assert resumed["phase_runs"]["R1"]["status"] == "passed"


def test_resume_recovers_r1_report_and_validated_task_written_before_runner_state(monkeypatch, tmp_path):
    module = _load_module()
    fixture = _fixture(tmp_path)
    run_root = tmp_path / "persisted-resume-run"
    crashed = False
    r1_calls: list[str] = []

    monkeypatch.setattr(
        module.ic_startup_retrieval,
        "generate_startup_retrieval_receipt",
        lambda deal_id, profile_id, *, round_name, **kwargs: _receipt(module, profile_id, round_name),
    )
    monkeypatch.setattr(
        module.ic_startup_retrieval,
        "current_evidence_identity",
        lambda *args, **kwargs: {"evidence_snapshot_hash": "a" * 64, "source_ids": ["SRC-SMOKE-001"]},
    )

    async def fake_r0(*args, **kwargs):
        profile_id = module.ic_phase_orchestrator.COORDINATOR_AGENT_ID
        return {"status": "completed", "hermes_called": True, "workflow_advanced": True, "task": _task(profile_id, "R0")}

    async def crash_after_persisting_r1(deal_id, profile_id, **kwargs):
        nonlocal crashed
        r1_calls.append(profile_id)
        task = _task(profile_id, _r1_phase(profile_id), round_name="R1")
        if profile_id == "siq_ic_strategist" and not crashed:
            crashed = True
            package_dir = module.deal_store.safe_deal_dir(deal_id, wiki_root=kwargs["wiki_root"])
            _write_json(
                package_dir / "phases/r1_reports.json",
                {
                    "reports": {
                        profile_id: {
                            "agent_id": profile_id,
                            "task_id": task["task_id"],
                            "status": "completed",
                            "hermes_called": True,
                            "evidence_snapshot_hash": "a" * 64,
                        }
                    }
                },
            )
            _write_json(
                package_dir / module.ic_phase_orchestrator.TASK_STORE_PATH,
                {"schema_version": "siq_ic_agent_tasks_v1", "tasks": [task]},
            )
            raise RuntimeError("crash after formal R1 artifacts were persisted")
        return {
            "status": "completed",
            "hermes_called": True,
            "report_written": True,
            "workflow_advanced": True,
            "phase_task_envelope": task,
        }

    monkeypatch.setattr(module.ic_agent_runtime, "run_workflow_r0_model", fake_r0)
    monkeypatch.setattr(module.ic_agent_runtime, "run_workflow_r1_agent", crash_after_persisting_r1)

    first, _ = asyncio.run(
        module.run_real(
            fixture=fixture,
            run_root=run_root,
            phases=["R0", "R1"],
            profiles=list(module.ic_policy.IC_PROFILE_IDS),
            resume=False,
            timeout=30,
            retrieval_limit=5,
        )
    )
    resumed, _ = asyncio.run(
        module.run_real(
            fixture=fixture,
            run_root=run_root,
            phases=["R0", "R1"],
            profiles=list(module.ic_policy.IC_PROFILE_IDS),
            resume=True,
            timeout=30,
            retrieval_limit=5,
        )
    )

    assert first["status"] == "failed"
    assert resumed["status"] == "passed"
    assert resumed["errors"] == []
    assert r1_calls.count("siq_ic_strategist") == 1
    strategist = resumed["profile_results"]["siq_ic_strategist"]
    assert strategist["task_count"] == 1
    assert strategist["phases"] == ["R1A"]
    assert resumed["phase_runs"]["R1"]["completed_profiles"] == list(module.ic_policy.R1_AGENT_SEQUENCE)


def test_task_extraction_requires_real_success_and_contract_validation():
    module = _load_module()
    profile_id = module.ic_phase_orchestrator.COORDINATOR_AGENT_ID
    successful = _task(profile_id, "R0")
    failed = {**_task(profile_id, "R0"), "task_id": "ICTASK-FAILED", "status": "failed"}
    unvalidated = {
        **_task(profile_id, "R0"),
        "task_id": "ICTASK-UNVALIDATED",
        "validated_output": {"schema_version": "forged_schema_cannot_override_contract"},
        "contract_validation": {
            "passed": False,
            "validated_by": "ic_phase_orchestrator",
            "output_schema": "siq_ic_r0_readiness_v1",
            "artifact_schema": None,
            "error_type": "ContractValidationError",
        },
    }

    records = module._task_records(
        {"task_results": [{"task": successful}, {"task": failed}, {"task": unvalidated}]},
        allowed_profiles={profile_id},
    )

    assert {item["task_id"] for item in records} == {"ICTASK-siq_ic_master_coordinator-R0", "ICTASK-UNVALIDATED"}
    successful_record = next(
        item for item in records if item["task_id"] == successful["task_id"]
    )
    assert successful_record["model_execution_audit"] == successful["model_execution_audit"]
    failed_validation = next(
        item for item in records if item["task_id"] == "ICTASK-UNVALIDATED"
    )["contract_validation"]
    assert failed_validation == unvalidated["contract_validation"]


def test_task_extraction_preserves_r15_null_artifact_schema():
    module = _load_module()
    chairman = module.ic_phase_orchestrator.CHAIRMAN_AGENT_ID
    task = _task(chairman, "R1.5")

    records = module._task_records({"task": task}, allowed_profiles={chairman})

    assert len(records) == 1
    assert records[0]["contract_validation"] == task["contract_validation"]
    assert records[0]["contract_validation"]["artifact_schema"] is None


def test_real_smoke_report_rejects_unverified_model_execution_identity():
    module = _load_module()
    state = module._empty_state(
        run_id="SMOKE-UNVERIFIED-RUNTIME",
        deal_id="DEAL-REAL-SMOKE-001",
        phases=["R0"],
        profiles=list(module.ic_policy.IC_PROFILE_IDS),
    )
    state["phase_runs"]["R0"] = {
        "status": "passed",
        "hermes_called": True,
        "workflow_advanced": True,
    }
    for profile_id in module.ic_policy.IC_PROFILE_IDS:
        state["agent_retrievals"][profile_id] = module.sanitize_receipt(
            _receipt(module, profile_id, "R0")
        )
        task = _task(profile_id, "R0")
        if profile_id == module.ic_phase_orchestrator.COORDINATOR_AGENT_ID:
            task["model_execution_audit"]["runtime_metadata_status"] = "unverified"
            task["model_execution_audit"]["attempts"][0]["runtime_metadata_status"] = "unverified"
            task["model_execution_audit"]["attempts"][0]["runtime"] = None
            task["model_execution_audit"]["final_runtime"] = None
        state["profile_tasks"][profile_id] = module._task_records(
            {"task": task},
            allowed_profiles={profile_id},
        )

    report = module.build_report(state, execution_mode="real")

    assert report["status"] == "failed"
    assert report["contract_validation"]["passed"] is False
    assert (
        "siq_ic_master_coordinator:model_execution_identity_unverified"
        in report["contract_validation"]["errors"]
    )


def test_r1_task_identity_requires_formal_subphase_and_r1_round():
    module = _load_module()
    profile_id = "siq_ic_strategist"
    records = module._task_records(
        {
            "tasks": [
                _task(profile_id, "R1A", round_name="R1"),
                _task(profile_id, "R1B", round_name="R1"),
                _task(profile_id, "R1", round_name="R1"),
                {**_task(profile_id, "R1A", round_name="R2"), "task_id": "ICTASK-WRONG-ROUND"},
            ]
        },
        allowed_profiles={profile_id},
    )

    accepted = module._r1_records_for_profile(records, profile_id)

    assert {(item["phase"], item["round_name"]) for item in accepted} == {("R1A", "R1"), ("R1B", "R1")}


def test_r2_profile_filter_fails_before_any_execution():
    module = _load_module()

    with pytest.raises(ValueError, match="requires all profiles"):
        module._phase_selected_profiles("R2", ["siq_ic_strategist"])


def test_validated_phase_task_is_reported_blocked_when_workflow_does_not_advance(
    monkeypatch,
    tmp_path,
):
    module = _load_module()
    fixture = _fixture(tmp_path)
    wiki_root = tmp_path / "wiki"
    package = wiki_root / "deals" / fixture.name
    package.parent.mkdir(parents=True)
    shutil.copytree(fixture, package)
    chairman = module.ic_phase_orchestrator.CHAIRMAN_AGENT_ID
    state = module._empty_state(
        run_id="SMOKE-BLOCKED-R15",
        deal_id=fixture.name,
        phases=["R1.5", "R2"],
        profiles=list(module.ic_policy.IC_PROFILE_IDS),
    )
    monkeypatch.setattr(
        module,
        "_prepare_receipt",
        lambda **_kwargs: module.sanitize_receipt(_receipt(module, chairman, "R1.5")),
    )

    async def fake_r15(*_args, **_kwargs):
        task = _task(chairman, "R1.5")
        task["output_schema"] = "siq_ic_r1_5_chairman_rulings_v2"
        return {
            "status": "needs_more_evidence",
            "hermes_called": True,
            "workflow_advanced": False,
            "task": task,
        }

    monkeypatch.setattr(module.ic_agent_runtime, "run_workflow_r1_5_model", fake_r15)

    asyncio.run(
        module._execute_phase(
            "R1.5",
            deal_id=fixture.name,
            profiles=[chairman],
            wiki_root=wiki_root,
            timeout=30,
            state=state,
            package_dir=package,
            retrieval_limit=5,
        )
    )
    report = module.build_report(state, execution_mode="real")

    assert state["phase_runs"]["R1.5"]["status"] == "blocked"
    assert state["phase_runs"]["R1.5"]["task_validated"] is True
    assert state["phase_runs"]["R1.5"]["workflow_advanced"] is False
    assert state["phase_runs"]["R1.5"]["workflow_blocked"] is True
    assert report["status"] == "blocked"
    assert report["workflow_blocked"] is True
    assert report["hermes_called"] is True
    assert report["contract_validation"]["passed"] is False
    assert report["phase_runs"]["R1.5"] == {
        "status": "blocked",
        "started_at": state["phase_runs"]["R1.5"]["started_at"],
        "completed_at": state["phase_runs"]["R1.5"]["completed_at"],
        "hermes_called": True,
        "task_validated": True,
        "result_status": "needs_more_evidence",
        "workflow_advanced": False,
        "workflow_blocked": True,
    }
    assert "phase:R1.5:blocked" in report["contract_validation"]["errors"]


def test_report_fail_closes_legacy_passed_state_when_workflow_did_not_advance():
    module = _load_module()
    state = module._empty_state(
        run_id="SMOKE-LEGACY-BLOCKED-R15",
        deal_id="DEAL-REAL-SMOKE-001",
        phases=["R1.5"],
        profiles=list(module.ic_policy.IC_PROFILE_IDS),
    )
    state["phase_runs"]["R1.5"] = {
        "status": "passed",
        "hermes_called": True,
        "result_status": "needs_more_evidence",
        "workflow_advanced": False,
    }
    for profile_id in module.ic_policy.IC_PROFILE_IDS:
        state["agent_retrievals"][profile_id] = module.sanitize_receipt(
            _receipt(module, profile_id, "R1.5")
        )
        state["profile_tasks"][profile_id] = module._task_records(
            {"task": _task(profile_id, "R1.5")},
            allowed_profiles={profile_id},
        )

    report = module.build_report(state, execution_mode="real")

    assert report["status"] == "blocked"
    assert report["workflow_blocked"] is True
    assert report["contract_validation"]["passed"] is False
    assert "phase:R1.5:blocked" in report["contract_validation"]["errors"]
    assert report["phase_runs"]["R1.5"]["status"] == "blocked"
    assert report["phase_runs"]["R1.5"]["workflow_advanced"] is False
    assert report["phase_runs"]["R1.5"]["workflow_blocked"] is True
