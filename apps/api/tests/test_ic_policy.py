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
    assert ic_policy.canonical_ic_profile_id("ic_finance_auditor") == "siq_ic_finance_auditor"
    assert ic_policy.canonical_ic_profile_id("siq_ic_finance_auditor") == "siq_ic_finance_auditor"


def test_public_ic_policy_redacts_local_directories():
    public_policy = ic_policy.public_ic_workflow_policy()
    serialized = json.dumps(public_policy, ensure_ascii=False)

    assert public_policy["r1_agent_sequence"] == list(ic_policy.R1_AGENT_SEQUENCE)
    assert "directories" not in public_policy
    assert "/home/" not in serialized
    assert "data/wiki/companies" not in serialized


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
