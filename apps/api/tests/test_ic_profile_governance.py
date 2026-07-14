from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SHARED_ROOT = REPO_ROOT / "agents" / "hermes" / "profiles" / "siq_ic_shared"


def _json(name: str) -> dict:
    return json.loads((SHARED_ROOT / name).read_text(encoding="utf-8"))


def test_behavior_migration_matrix_does_not_overstate_r2_r3_r4_quality() -> None:
    matrix = _json("openclaw_script_migration_matrix.json")
    assert matrix["schema_version"] == "siq_ic_openclaw_behavior_migration_matrix_v2"
    assert set(matrix["parity_definitions"]) == {
        "asset_migrated",
        "contract_migrated",
        "behavior_migrated",
        "quality_accepted",
    }
    entries = {item["behavior_id"]: item for item in matrix["behavior_entries"]}
    required = {
        "retrieval.startup.role_scoped",
        "r0.coordinator.readiness_and_scope",
        "r1.independent_expert_research",
        "r1_5.chairman_ruling_and_followup",
        "r2.expert_revision_with_delta",
        "r3.red_blue.full",
        "r4.chairman_structured_decision",
        "report.complete_render_factcheck_and_repair",
    }
    assert required <= set(entries)
    for behavior_id in (
        "r2.expert_revision_with_delta",
        "r3.red_blue.full",
        "r4.chairman_structured_decision",
        "report.complete_render_factcheck_and_repair",
    ):
        assert entries[behavior_id]["parity_level"] == "contract_migrated"
        assert entries[behavior_id]["quality_accepted"] is False
        assert entries[behavior_id]["known_gaps"]
    assert not any(item["parity_level"] == "quality_accepted" for item in entries.values())


def test_behavior_migration_matrix_acceptance_test_paths_exist() -> None:
    matrix = _json("openclaw_script_migration_matrix.json")
    for entry in matrix["behavior_entries"]:
        acceptance_tests = entry.get("acceptance_tests", [])
        assert acceptance_tests, f"missing acceptance tests: {entry['behavior_id']}"
        for relative_path in acceptance_tests:
            path = Path(relative_path)
            assert not path.is_absolute(), f"acceptance test path must be repo-relative: {relative_path}"
            assert (REPO_ROOT / path).is_file(), (
                f"missing acceptance test for {entry['behavior_id']}: {relative_path}"
            )


def test_behavior_migration_matrix_golden_cases_exist_in_registry() -> None:
    matrix = _json("openclaw_script_migration_matrix.json")
    registry_path = Path(matrix["golden_case_registry"])
    assert not registry_path.is_absolute()
    assert (REPO_ROOT / registry_path).is_file()

    registry = json.loads((REPO_ROOT / registry_path).read_text(encoding="utf-8"))
    case_ids = [item["case_id"] for item in registry["cases"]]
    assert len(case_ids) == len(set(case_ids)), "golden case registry contains duplicate case IDs"
    known_case_ids = set(case_ids)

    for entry in matrix["behavior_entries"]:
        unknown = sorted(set(entry.get("golden_cases", [])) - known_case_ids)
        assert not unknown, f"unknown golden cases for {entry['behavior_id']}: {unknown}"


def test_all_ic_profiles_require_distinct_private_background_collections() -> None:
    matrix = _json("ic_profile_matrix.json")
    assert matrix["schema_version"] == 2
    assert matrix["private_collection_rule"]["required"] is True
    assert matrix["private_collection_rule"]["source_class"] == "background_knowledge"
    profiles = matrix["profiles"]
    assert len(profiles) == 7
    private_collections: list[str] = []
    for profile in profiles:
        retrieval = profile["retrieval"]
        assert retrieval["required"] is True
        assert retrieval["logical_collections"][0] == "siq_deal_shared"
        assert retrieval["physical_collections"][0] == "ic_collaboration_shared"
        assert retrieval["private_collection"] == retrieval["physical_collections"][1]
        assert profile["phase_capabilities"]
        assert profile["responsibilities"]
        assert profile["boundaries"]
        private_collections.append(retrieval["private_collection"])
    assert len(set(private_collections)) == 7


def test_all_profile_output_schema_paths_exist_and_match_schema_ids() -> None:
    matrix = _json("ic_profile_matrix.json")
    for profile in matrix["profiles"]:
        for relative_path in profile.get("output_schemas", {}).values():
            path = SHARED_ROOT / relative_path
            assert path.is_file(), f"missing profile schema: {relative_path}"
            schema = json.loads(path.read_text(encoding="utf-8"))
            assert path.name == f"{schema['$id']}.schema.json"


def test_profile_instructions_enforce_private_collection_and_source_classification() -> None:
    matrix = _json("ic_profile_matrix.json")
    profiles_root = SHARED_ROOT.parent
    for profile in matrix["profiles"]:
        instructions = (profiles_root / profile["id"] / "AGENTS.md").read_text(encoding="utf-8")
        private_collection = profile["retrieval"]["private_collection"]
        assert private_collection in instructions
        assert "project_evidence" in instructions
        assert "background_knowledge" in instructions


def test_phase_templates_enforce_source_classes_and_fallback_identity() -> None:
    task_root = SHARED_ROOT / "tasks"
    required = {
        "R0_COORDINATOR_READINESS.md",
        "R1_INDEPENDENT_RESEARCH.md",
        "R1_CROSS_VALIDATION.md",
        "R1_5_CHAIRMAN_RULING.md",
        "R2_EXPERT_REVISION.md",
        "R3_RED_BLUE_DEBATE.md",
        "R4_CHAIRMAN_DECISION.md",
        "DETERMINISTIC_FALLBACK.md",
    }
    assert required <= {path.name for path in task_root.glob("*.md")}
    combined = "\n".join((task_root / name).read_text(encoding="utf-8") for name in sorted(required))
    assert "project_evidence" in combined
    assert "background_knowledge" in combined
    assert "deterministic fallback" in combined.lower()
    assert "quality_accepted" in (task_root / "README.md").read_text(encoding="utf-8")


def test_golden_case_manifest_is_candidate_only() -> None:
    manifest = _json("golden_case_manifest.json")
    assert manifest["acceptance_status"] == "candidates_only"
    assert manifest["quality_accepted"] is False
    assert len(manifest["cases"]) >= 5
    assert all(item["status"] == "candidate" for item in manifest["cases"])
    assert all(item["quality_accepted"] is False for item in manifest["cases"])
    assert any(item["case_id"] == "GOLDEN-PMIC-FULL-R3" for item in manifest["cases"])
    assert any(item["case_id"] == "GOLDEN-PMIC-ROLE-ROUTING" for item in manifest["cases"])
