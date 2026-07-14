from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
API_ROOT = PROJECT_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services import deal_decision, deal_store  # noqa: E402

ACTIVATION_PATH = PROJECT_ROOT / "scripts" / "maintenance" / "activate_primary_market_ic_stale_fixture.py"
FIXTURE = PROJECT_ROOT / "eval_datasets" / "primary_market_ic_real_smoke" / "DEAL-PMIC-SNAPSHOT-STALE-2026"


def _load_activation_module():
    spec = importlib.util.spec_from_file_location("pmic_stale_fixture_activation", ACTIVATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


activation = _load_activation_module()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _confirmed_package(
    tmp_path: Path,
    *,
    confirmation_status: str = "confirmed",
) -> tuple[Path, Path, str]:
    wiki_root = tmp_path / "wiki"
    package = wiki_root / "deals" / FIXTURE.name
    package.parent.mkdir(parents=True)
    shutil.copytree(FIXTURE, package)
    snapshot = json.loads((package / "evidence/evidence_snapshot.json").read_text(encoding="utf-8"))
    snapshot_hash = snapshot["snapshot_hash"]
    decision = {
        "schema_version": "siq_ic_r4_decision_v2",
        "report_id": "ICRPT-PMIC-STALE-R4-001",
        "revision": 1,
        "workflow_run_id": "ICRUN-PMICSTALE2026",
        "deal_id": FIXTURE.name,
        "evidence_snapshot_hash": snapshot_hash,
        "generation_mode": "model",
        "decision": "conditional_support",
    }
    quality = {
        "schema_version": "siq_ic_report_quality_v1",
        "report_id": decision["report_id"],
        "report_revision": 1,
        "evidence_snapshot_hash": snapshot_hash,
        "status": "pass",
        "allowed_for_human_confirmation": True,
        "blocking_reasons": [],
    }
    factcheck = {
        "schema_version": "siq_ic_report_factcheck_v1",
        "report_id": decision["report_id"],
        "report_revision": 1,
        "evidence_snapshot_hash": snapshot_hash,
        "status": "pass",
    }
    confirmation = {
        "status": confirmation_status,
        "confirmed": confirmation_status == "confirmed",
        "confirmed_by": {"id": "human-001", "username": "trusted-reviewer"},
        "confirmed_at": "2026-07-14T00:30:00Z",
        "override_reason": (
            "Trusted reviewer selected a documented manual override." if confirmation_status == "overridden" else None
        ),
    }
    decision["human_confirmation"] = confirmation
    confirmation.update(
        deal_decision._confirmation_attestation(
            decision,
            quality=quality,
            factcheck=factcheck,
        )
    )
    _write_json(package / "phases/r4_decision.json", decision)
    _write_json(package / deal_decision.R4_QUALITY_PATH, quality)
    _write_json(package / deal_decision.R4_FACTCHECK_PATH, factcheck)
    completion = {key: confirmation.get(key) for key in deal_decision.HUMAN_CONFIRMATION_PROVENANCE_FIELDS}
    _write_json(
        package / deal_decision.WORKFLOW_RUNS_PATH,
        {
            "schema_version": "siq_ic_workflow_runs_v1",
            "runs": [
                {
                    "workflow_run_id": decision["workflow_run_id"],
                    "deal_id": FIXTURE.name,
                    "status": "completed",
                    "evidence_snapshot_hash": snapshot_hash,
                    "completed_at": confirmation["confirmed_at"],
                    "completion": completion,
                }
            ],
        },
    )
    _write_json(
        package / "phases/audit_log.json",
        {
            "events": [
                {
                    "event_type": "r4_human_confirmation_updated",
                    "created_at": "2026-07-14T00:30:01Z",
                    **{key: confirmation.get(key) for key in deal_decision.HUMAN_CONFIRMATION_AUDIT_FIELDS},
                }
            ]
        },
    )
    _write_json(
        package / "phases/startup_receipts.json",
        {
            "schema_version": "siq_ic_startup_receipts_v2",
            "deal_id": FIXTURE.name,
            "agents": {
                "siq_ic_chairman": {
                    "receipt_id": "startup-siq_ic_chairman-R4-001",
                    "evidence_snapshot_hash": snapshot_hash,
                    "readiness_status": "current",
                }
            },
        },
    )
    return wiki_root, package, snapshot_hash


def test_stale_fixture_activation_uses_normal_snapshot_invalidation(tmp_path: Path):
    wiki_root, package, previous_hash = _confirmed_package(tmp_path)

    result = activation.activate_stale_update(
        package,
        wiki_root=wiki_root,
        actor={"id": "operator-001", "username": "golden-operator"},
    )

    assert result["quality_accepted"] is False
    assert result["previous_snapshot_hash"] == previous_hash
    assert result["current_snapshot_hash"] != previous_hash
    assert result["workflow_status"] == "decision_review_required"
    assert result["idempotent_replay"] is False
    workflow = deal_store.read_json(package / "phases/workflow_state.json", {})
    assert workflow["decision_review_required"] is True
    assert workflow["confirmed_decision_snapshot_hash"] == previous_hash
    receipt = deal_store.read_json(package / "phases/startup_receipts.json", {})["agents"]["siq_ic_chairman"]
    assert receipt["readiness_status"] == "stale"
    assert receipt["current_evidence_snapshot_hash"] == result["current_snapshot_hash"]
    sources = deal_store.read_json(package / "sources/analysis_sources.json", {})["sources"]
    assert len(sources) == 2
    assert any(item["source_id"] == result["source_id"] for item in sources)

    replay = activation.activate_stale_update(
        package,
        wiki_root=wiki_root,
        actor={"id": "operator-002", "username": "another-operator"},
    )
    assert replay == {**result, "idempotent_replay": True}
    replay_sources = deal_store.read_json(package / "sources/analysis_sources.json", {})["sources"]
    assert replay_sources == sources


def test_stale_fixture_activation_rejects_tampered_staged_content(tmp_path: Path):
    wiki_root, package, _ = _confirmed_package(tmp_path)
    staged_content = package / "scenario_inputs/stale_update/content_list_enhanced.json"
    staged_content.write_text('{"tampered":true}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="content digest mismatch"):
        activation.activate_stale_update(package, wiki_root=wiki_root)

    sources = deal_store.read_json(package / "sources/analysis_sources.json", {})["sources"]
    assert len(sources) == 1


def test_human_confirmation_attestation_detects_quality_tampering(tmp_path: Path):
    _, package, _ = _confirmed_package(tmp_path)
    decision = deal_store.read_json(package / "phases/r4_decision.json", {})
    quality = deal_store.read_json(package / deal_decision.R4_QUALITY_PATH, {})
    factcheck = deal_store.read_json(package / deal_decision.R4_FACTCHECK_PATH, {})
    quality["status"] = "fail"

    with pytest.raises(ValueError, match="quality_sha256"):
        deal_decision.validate_human_confirmation_attestation(
            decision,
            quality=quality,
            factcheck=factcheck,
        )


def test_human_confirmation_attestation_rejects_rehashed_ineligible_quality(
    tmp_path: Path,
):
    _, package, _ = _confirmed_package(tmp_path)
    decision = deal_store.read_json(package / "phases/r4_decision.json", {})
    quality = deal_store.read_json(package / deal_decision.R4_QUALITY_PATH, {})
    factcheck = deal_store.read_json(package / deal_decision.R4_FACTCHECK_PATH, {})
    quality["status"] = "fail"
    quality["allowed_for_human_confirmation"] = False
    quality["blocking_reasons"] = ["deterministic_quality_failed"]
    decision["human_confirmation"].update(
        deal_decision._confirmation_attestation(
            decision,
            quality=quality,
            factcheck=factcheck,
        )
    )

    with pytest.raises(ValueError, match="quality contract is not eligible"):
        deal_decision.validate_human_confirmation_attestation(
            decision,
            quality=quality,
            factcheck=factcheck,
        )


def test_human_confirmation_attestation_rejects_approval_like_extra_field(
    tmp_path: Path,
):
    _, package, _ = _confirmed_package(tmp_path)
    decision = deal_store.read_json(package / "phases/r4_decision.json", {})
    quality = deal_store.read_json(package / deal_decision.R4_QUALITY_PATH, {})
    factcheck = deal_store.read_json(package / deal_decision.R4_FACTCHECK_PATH, {})
    decision["human_confirmation"]["quality_accepted"] = True

    with pytest.raises(ValueError, match="unexpected fields"):
        deal_decision.validate_human_confirmation_attestation(
            decision,
            quality=quality,
            factcheck=factcheck,
        )


def test_stale_fixture_activation_rejects_document_path_escape_before_mutation(
    tmp_path: Path,
):
    wiki_root, package, _ = _confirmed_package(tmp_path)
    descriptor_path = package / "scenario_inputs/stale_update.json"
    descriptor = deal_store.read_json(descriptor_path, {})
    descriptor["source"]["document_id"] = "../../escaped"
    _write_json(descriptor_path, descriptor)

    with pytest.raises(ValueError, match="one relative path segment"):
        activation.activate_stale_update(package, wiki_root=wiki_root)

    assert not (tmp_path / "escaped").exists()
    sources = deal_store.read_json(package / "sources/analysis_sources.json", {})["sources"]
    assert len(sources) == 1


def test_stale_fixture_activation_rejects_source_conflict_before_copy(tmp_path: Path):
    wiki_root, package, _ = _confirmed_package(tmp_path)
    descriptor = deal_store.read_json(package / "scenario_inputs/stale_update.json", {})
    registry_path = package / "sources/analysis_sources.json"
    registry = deal_store.read_json(registry_path, {})
    registry["sources"].append(
        {
            **descriptor["source"],
            "artifact_manifest_path": "different/archive_manifest.json",
        }
    )
    _write_json(registry_path, registry)

    with pytest.raises(ValueError, match="already exists with different content"):
        activation.activate_stale_update(package, wiki_root=wiki_root)

    source = descriptor["source"]
    final_dir = package / "parsed_documents" / source["document_id"] / "runs" / source["parse_run_id"]
    assert not final_dir.exists()


def test_stale_fixture_activation_rejects_confirmation_actor_tampering(tmp_path: Path):
    wiki_root, package, _ = _confirmed_package(tmp_path)
    decision_path = package / "phases/r4_decision.json"
    decision = deal_store.read_json(decision_path, {})
    decision["human_confirmation"]["confirmed_by"] = {
        "id": "attacker",
        "username": "forged-reviewer",
    }
    _write_json(decision_path, decision)

    with pytest.raises(ValueError, match="workflow attestation mismatch"):
        activation.activate_stale_update(package, wiki_root=wiki_root)


def test_overridden_confirmation_is_also_invalidated_by_stale_source(tmp_path: Path):
    wiki_root, package, previous_hash = _confirmed_package(
        tmp_path,
        confirmation_status="overridden",
    )

    result = activation.activate_stale_update(package, wiki_root=wiki_root)

    assert result["previous_snapshot_hash"] == previous_hash
    assert result["current_snapshot_hash"] != previous_hash
    assert result["workflow_status"] == "decision_review_required"
    assert result["quality_accepted"] is False


def test_stale_fixture_activation_rejects_override_reason_tampering(tmp_path: Path):
    wiki_root, package, _ = _confirmed_package(
        tmp_path,
        confirmation_status="overridden",
    )
    decision_path = package / "phases/r4_decision.json"
    decision = deal_store.read_json(decision_path, {})
    decision["human_confirmation"]["override_reason"] = "forged replacement reason"
    _write_json(decision_path, decision)

    with pytest.raises(ValueError, match="audit provenance"):
        activation.activate_stale_update(package, wiki_root=wiki_root)
