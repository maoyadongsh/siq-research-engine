import importlib.util
import json
from pathlib import Path

import pytest


def load_module():
    path = Path(__file__).resolve().parents[1] / "plan_agent_memory_milvus_migration.py"
    spec = importlib.util.spec_from_file_location("agent_memory_milvus_migration", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def contract_snapshot(module):
    return json.loads(module.DEFAULT_SNAPSHOT.read_text(encoding="utf-8"))


def test_contract_snapshot_builds_blocked_non_destructive_v1_to_v2_plan():
    module = load_module()

    report = module.build_migration_plan(contract_snapshot(module))

    assert report["passed"] is True
    assert report["dry_run"] is True
    assert report["writes_performed"] is False
    assert report["planner_live_milvus_contacted"] is False
    assert report["source_inventory_live_milvus_contacted"] is False
    assert report["migration_ready"] is False
    assert report["blocking_reasons"] == [
        "redacted_read_only_inventory_required",
        "partial_research_identity_backfill_required",
    ]
    assert report["snapshot_kind"] == "synthetic_contract"
    assert report["live_milvus_contacted"] is False
    assert report["source"]["entity_count"] == 120
    assert report["source"]["missing_v2_fields"] == [
        "research_company_id",
        "research_filing_id",
        "research_market",
        "research_parse_run_id",
    ]
    assert report["target"]["schema_version"] == "siq_agent_memory_milvus_v2"
    assert report["target"]["expected_entity_count"] == 120
    assert report["target"]["vector_dimension"] == 1024
    assert report["target"]["expected_id_content_hash_manifest_sha256"] == "a" * 64
    assert report["identity_backfill"]["partial_count"] == 10
    assert report["identity_backfill"]["observation_status"] == "observed"
    assert report["alias_plan"]["bootstrap_required"] is False
    assert report["rollback_manifest"]["restore_collection"] == "siq_agent_memory"
    assert report["rollback_manifest"]["destructive_actions"] == []
    assert "drop source collection" in report["prohibited_actions"]


def test_complete_identity_snapshot_is_ready_when_alias_points_to_source():
    module = load_module()
    snapshot = contract_snapshot(module)
    snapshot["identity"].update(
        {
            "research_scoped_count": 50,
            "complete_count": 50,
            "partial_count": 0,
            "unscoped_count": 70,
            "missing_by_field": {"market": 0, "company_id": 0, "filing_id": 0, "parse_run_id": 0},
        }
    )
    snapshot["snapshot_kind"] = "redacted_read_only_inventory"

    report = module.build_migration_plan(snapshot)

    assert report["migration_ready"] is True
    assert report["blocking_reasons"] == []


def test_planner_treats_alias_already_on_target_as_switched_not_bootstrap():
    module = load_module()
    snapshot = contract_snapshot(module)
    snapshot["snapshot_kind"] = "redacted_read_only_inventory"
    snapshot["identity"].update(
        {
            "research_scoped_count": 50,
            "complete_count": 50,
            "partial_count": 0,
            "unscoped_count": 70,
            "missing_by_field": {"market": 0, "company_id": 0, "filing_id": 0, "parse_run_id": 0},
        }
    )
    snapshot["aliases"] = [{"name": "siq_agent_memory_active", "collection": "siq_agent_memory__v2"}]

    report = module.build_migration_plan(snapshot)

    assert report["migration_ready"] is True
    assert report["alias_plan"]["bootstrap_required"] is False
    assert report["alias_plan"]["already_on_target"] is True
    assert report["alias_plan"]["switch_from"] == "siq_agent_memory__v2"


def test_plan_rejects_unsafe_names_and_inconsistent_counts():
    module = load_module()
    snapshot = contract_snapshot(module)

    with pytest.raises(ValueError, match="target collection must differ"):
        module.build_migration_plan(snapshot, target_collection="siq_agent_memory")

    snapshot["identity"]["partial_count"] = 9
    with pytest.raises(ValueError, match="research_scoped_count"):
        module.build_migration_plan(snapshot)


def test_unavailable_v1_identity_inventory_keeps_unknown_counts_and_blocks_migration():
    module = load_module()
    snapshot = contract_snapshot(module)
    snapshot["snapshot_kind"] = "redacted_read_only_inventory"
    snapshot["provenance"] = {"source_inventory_live_milvus_contacted": True}
    snapshot["identity"] = {
        "observation_status": "unavailable",
        "observation_reason": "v1 scalar ResearchIdentity fields do not exist and metadata was not scanned",
        "research_scoped_count": None,
        "complete_count": None,
        "partial_count": None,
        "unscoped_count": None,
        "missing_by_field": {
            "market": None,
            "company_id": None,
            "filing_id": None,
            "parse_run_id": None,
        },
    }

    report = module.build_migration_plan(snapshot)

    assert report["migration_ready"] is False
    assert report["planner_live_milvus_contacted"] is False
    assert report["source_inventory_live_milvus_contacted"] is True
    assert report["blocking_reasons"] == ["identity_inventory_unavailable"]
    assert report["identity_backfill"]["observation_status"] == "unavailable"
    assert report["identity_backfill"]["partial_count"] is None
    assert report["identity_backfill"]["missing_by_field"] == {
        "market": None,
        "company_id": None,
        "filing_id": None,
        "parse_run_id": None,
    }
    assert "never infer identity" in report["identity_backfill"]["policy"]["inference"]


def test_unavailable_identity_inventory_rejects_zero_as_unknown():
    module = load_module()
    snapshot = contract_snapshot(module)
    snapshot["identity"].update(
        {
            "observation_status": "unavailable",
            "observation_reason": "v1 scalar fields absent",
            "research_scoped_count": 0,
            "complete_count": 0,
            "partial_count": 0,
            "unscoped_count": 0,
            "missing_by_field": {"market": None, "company_id": None, "filing_id": None, "parse_run_id": None},
        }
    )

    with pytest.raises(ValueError, match="must be null, not zero or inferred"):
        module.build_migration_plan(snapshot)


def test_snapshot_rejects_zero_vector_dimension():
    module = load_module()
    snapshot = contract_snapshot(module)
    snapshot["collection"]["vector_dimension"] = 0

    with pytest.raises(ValueError, match="vector_dimension must be a positive integer"):
        module.build_migration_plan(snapshot)


def test_cli_writes_contract_artifacts_without_opening_milvus(tmp_path, monkeypatch):
    module = load_module()
    output = tmp_path / "plan.json"
    markdown = tmp_path / "plan.md"
    monkeypatch.setattr(
        module.agent_memory_milvus,
        "_client",
        lambda: pytest.fail("offline migration planner must not open Milvus"),
    )

    result = module.main(
        [
            "--snapshot",
            str(module.DEFAULT_SNAPSHOT),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ]
    )

    assert result == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["writes_performed"] is False
    assert payload["live_milvus_contacted"] is False
    assert payload["migration_ready"] is False
    assert "Blocking Reasons" in markdown.read_text(encoding="utf-8")


def test_require_ready_fails_for_identity_backfill_gaps(tmp_path):
    module = load_module()

    result = module.main(
        [
            "--snapshot",
            str(module.DEFAULT_SNAPSHOT),
            "--require-ready",
            "--output",
            str(tmp_path / "plan.json"),
            "--markdown",
            str(tmp_path / "plan.md"),
        ]
    )

    assert result == 1
