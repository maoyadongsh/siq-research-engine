import json
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from routers import deals
from services import ic_policy


def test_ic_policy_reads_weights_thresholds_and_r1_sequence():
    policy = ic_policy.read_ic_workflow_policy()

    assert policy["weights"] == {
        "chairman": 0.3,
        "strategy": 0.15,
        "sector": 0.15,
        "finance": 0.15,
        "risk": 0.15,
        "legal": 0.1,
    }
    assert policy["thresholds"]["pass"] == 70
    assert policy["thresholds"]["review_min"] == 68
    assert policy["thresholds"]["review_max"] == 69
    assert list(ic_policy.R1_AGENT_SEQUENCE) == [
        "siq_ic_strategist",
        "siq_ic_sector_expert",
        "siq_ic_finance_auditor",
        "siq_ic_legal_scanner",
        "siq_ic_risk_controller",
        "siq_ic_chairman",
    ]


def test_ic_profile_readiness_contract():
    profiles = ic_policy.list_ic_profiles()

    assert [profile["id"] for profile in profiles] == list(ic_policy.IC_PROFILE_IDS)
    finance = next(profile for profile in profiles if profile["id"] == "siq_ic_finance_auditor")
    assert finance["role"] == "finance"
    assert finance["default_port"] == 18664
    assert finance["config_exists"] is True
    assert finance["in_manifest"] is True
    assert finance["in_manifest_group"] is True
    assert "ic_finance" in finance["aliases"]
    assert finance["profile_path"] == "agents/hermes/profiles/siq_ic_finance_auditor"
    assert finance["r1_sequence_index"] == 2
    assert ic_policy.canonical_ic_profile_id("ic_finance") == "siq_ic_finance_auditor"
    assert ic_policy.canonical_ic_profile_id("ic_finance_auditor") == "siq_ic_finance_auditor"
    assert ic_policy.canonical_ic_profile_id("siq_ic_finance_auditor") == "siq_ic_finance_auditor"


def test_public_ic_policy_redacts_local_directories():
    public_policy = ic_policy.public_ic_workflow_policy()
    serialized = json.dumps(public_policy, ensure_ascii=False)

    assert public_policy["r1_agent_sequence"] == list(ic_policy.R1_AGENT_SEQUENCE)
    assert "directories" not in public_policy
    assert "/home/" not in serialized
    assert "data/wiki/companies" not in serialized


def test_openclaw_script_migration_matrix_tracks_key_scripts():
    matrix = ic_policy.public_openclaw_script_migration_matrix()

    assert matrix["schema_version"] == "siq_ic_openclaw_script_migration_matrix_v1"
    assert matrix["counts"]["entries"] == len(matrix["entries"])
    assert sum(matrix["counts"]["by_status"].values()) == matrix["counts"]["entries"]
    assert matrix["counts"]["entries"] >= 37
    for status in ("migrated", "planned", "wrap_required", "reference_only", "do_not_migrate"):
        assert matrix["counts"]["by_status"][status] >= 1
    by_script = {item["script"].rsplit("/", 1)[-1]: item for item in matrix["entries"]}
    assert by_script["embedding_client.py"]["status"] == "migrated"
    assert by_script["knowledge_ingestor.py"]["siq_target"].startswith("scripts/vector-index/milvus-ingestion")
    assert by_script["r1_serial_dispatcher.py"]["status"] == "planned"
    assert by_script["weighted_scoring.py"]["status"] == "planned"
    assert by_script["qcc_client.py"]["status"] == "wrap_required"
    assert "/home/maoyd" not in json.dumps(matrix, ensure_ascii=False)


def test_openclaw_script_migration_payload_counts_filtered_entries_and_defaults():
    payload = ic_policy.public_openclaw_script_migration_matrix_payload(
        {
            "schema_version": "matrix-v1",
            "updated_at": "2026-07-03",
            "purpose": "fixture",
            "entries": [
                {"script": "a.py", "status": "planned", "category": "workflow", "owner": "deal"},
                {"script": "b.py", "status": "migrated", "category": "retrieval"},
                {"script": "c.py", "owner": "platform"},
                ["not", "an", "entry"],
            ],
        }
    )

    assert payload["schema_version"] == "matrix-v1"
    assert payload["source_scope"] == []
    assert payload["status_definitions"] == {}
    assert payload["counts"] == {
        "entries": 3,
        "by_status": {"migrated": 1, "planned": 1, "unknown": 1},
        "by_category": {"retrieval": 1, "unknown": 1, "workflow": 1},
        "by_owner": {"deal": 1, "platform": 1, "unknown": 1},
    }
    assert [item["script"] for item in payload["entries"]] == ["a.py", "b.py", "c.py"]

    empty_payload = ic_policy.public_openclaw_script_migration_matrix_payload({})
    assert empty_payload["source_scope"] == []
    assert empty_payload["status_definitions"] == {}
    assert empty_payload["counts"] == {
        "entries": 0,
        "by_status": {},
        "by_category": {},
        "by_owner": {},
    }
    assert empty_payload["entries"] == []


def test_deals_ic_profiles_endpoint_is_read_only_readiness():
    result = deals.get_ic_profiles(runtime=False, current_user=SimpleNamespace(id=1, username="viewer"))

    assert len(result["profiles"]) == 7
    chairman = next(profile for profile in result["profiles"] if profile["id"] == "siq_ic_chairman")
    assert chairman["default_port"] == 18661
    assert chairman["config_exists"] is True
    assert "base" not in chairman
    assert "runtime" not in chairman


def test_deals_ic_policy_endpoint_uses_public_contract():
    result = deals.get_ic_policy(current_user=SimpleNamespace(id=1, username="viewer"))

    assert result["policy"]["thresholds"]["pass"] == 70
    assert result["policy"]["r1_agent_sequence"][0] == "siq_ic_strategist"
    assert "directories" not in result["policy"]


def test_deals_ic_script_migration_endpoint_uses_public_matrix():
    result = deals.get_ic_script_migration(current_user=SimpleNamespace(id=1, username="viewer"))

    assert result["matrix"]["counts"]["entries"] >= 37
    assert result["matrix"]["counts"]["by_status"]["do_not_migrate"] >= 1
    assert any(
        item["script"].endswith("startup_retrieval.py") and item["status"] == "migrated"
        for item in result["matrix"]["entries"]
    )
