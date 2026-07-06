import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services import ic_policy
from services import ic_profile_contract
from services.path_config import PROJECT_ROOT


def test_ic_profile_contracts_cover_all_profiles_and_sources_exist():
    contracts = ic_profile_contract.list_ic_profile_contracts()

    assert [item["profile_id"] for item in contracts] == list(ic_policy.IC_PROFILE_IDS)
    assert {item["schema_version"] for item in contracts} == {"siq_ic_profile_contract_v1"}
    assert "/home/" not in json.dumps(contracts, ensure_ascii=False)

    for contract in contracts:
        assert contract["label"]
        assert contract["role"]
        assert contract["responsibilities"]
        assert contract["focus"]
        assert contract["outputs"]
        assert contract["boundaries"]
        assert len(contract["source_files"]) >= 4
        for relative_path in contract["source_files"]:
            assert relative_path.startswith(f"agents/hermes/profiles/{contract['profile_id']}/")
            assert (PROJECT_ROOT / relative_path).is_file()


def test_get_ic_profile_contract_uses_matrix_policy_and_profile_boundaries():
    finance = ic_profile_contract.get_ic_profile_contract("ic_finance")

    assert finance["profile_id"] == "siq_ic_finance_auditor"
    assert finance["label"] == "财务审计委员"
    assert finance["role"] == "finance"
    assert finance["role_title"] == "SIQ 投委会财务专家"
    assert finance["responsibilities"] == [
        "financial consistency",
        "unit economics",
        "valuation and forecast audit",
    ]
    assert "财务分析、估值模型、现金流、盈利模式" in finance["focus"]
    assert any("行业技术判断" in boundary for boundary in finance["boundaries"])
    assert finance["startup_retrieval_required"] is True
    assert finance["r1_sequence_index"] == 2

    master = ic_profile_contract.get_ic_profile_contract("siq_ic_master_coordinator")
    assert master["startup_retrieval_required"] is False
    assert master["r1_sequence_index"] is None


def test_render_meeting_role_guard_contains_role_contract_and_provenance():
    finance = ic_profile_contract.get_ic_profile_contract("siq_ic_finance_auditor")
    guard = ic_profile_contract.render_meeting_role_guard(finance)

    assert "一级市场 IC profile 职责护栏:" in guard
    assert "profile_id: siq_ic_finance_auditor" in guard
    assert "角色名称: SIQ 投委会财务专家" in guard
    assert "startup_retrieval_required: true" in guard
    assert "financial consistency" in guard
    assert "不做行业技术判断" in guard
    assert "agents/hermes/profiles/siq_ic_finance_auditor/AGENTS.md" in guard
    assert "若主持人问题要求越权" in guard
    assert "assumed/待核验" in guard


def test_unknown_ic_profile_contract_raises_value_error():
    with pytest.raises(ValueError):
        ic_profile_contract.get_ic_profile_contract("siq_ic_not_real")
