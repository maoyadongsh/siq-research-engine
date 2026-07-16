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
        assert contract["independent_services"]
        assert contract["skill_ids"]
        assert contract["namespace_policy"]["namespace"] == "primary_market"
        assert contract["namespace_policy"]["allowed_roots"] == ["data/wiki/deals/{deal_id}"]
        assert contract["namespace_policy"]["forbidden_roots"] == ["data/wiki/companies"]
        assert contract["contract_version"] == "siq_ic_profile_matrix_v2"
        assert contract["phase_capabilities"]
        assert contract["output_schemas"]
        assert contract["retrieval"]["required"] is True
        assert contract["retrieval"]["private_collection"]
        assert len(contract["retrieval_physical_collections"]) == 2
        assert len(contract["source_files"]) >= 5
        assert any(path.endswith("/TOOLS.md") for path in contract["source_files"])
        for relative_path in contract["source_files"]:
            assert relative_path.startswith(f"agents/hermes/profiles/{contract['profile_id']}/")
            assert (PROJECT_ROOT / relative_path).is_file()


def test_get_ic_profile_contract_uses_matrix_policy_and_profile_boundaries():
    finance = ic_profile_contract.get_ic_profile_contract("ic_finance")

    assert finance["profile_id"] == "siq_ic_finance_auditor"
    assert finance["label"] == "财务审计委员"
    assert finance["role"] == "finance"
    assert finance["role_title"] == "SIQ 投委会财务专家"
    assert any("historical financials" in item for item in finance["responsibilities"])
    assert any("period, currency, unit" in item for item in finance["responsibilities"])
    assert "财务分析、估值模型、现金流、盈利模式" in finance["focus"]
    assert any("行业技术判断" in boundary for boundary in finance["boundaries"])
    assert finance["startup_retrieval_required"] is True
    assert finance["r1_sequence_index"] == 2

    master = ic_profile_contract.get_ic_profile_contract("siq_ic_master_coordinator")
    assert master["startup_retrieval_required"] is True
    assert master["r1_sequence_index"] is None
    assert finance["phase_capabilities"]["R1A"] == ["independent_finance_report", "numeric_trace"]
    assert finance["output_schemas"]["R2"].endswith("siq_ic_r2_revision_v1.schema.json")
    assert finance["private_knowledge_collection"] == "siq_ic_finance_auditor"
    assert finance["private_physical_collection"] == "ic_finance_auditor"
    assert finance["shared_collection"] == "siq_deal_shared"
    assert finance["shared_physical_collection"] == "ic_collaboration_shared"
    assert "agent_runtime_financial_claim_verifier" in finance["independent_services"]
    assert "ic-finance-auditor" in finance["skill_ids"]


def test_render_meeting_role_guard_contains_role_contract_and_provenance():
    finance = ic_profile_contract.get_ic_profile_contract("siq_ic_finance_auditor")
    guard = ic_profile_contract.render_meeting_role_guard(finance)

    assert "一级市场 IC profile 职责护栏:" in guard
    assert "profile_id: siq_ic_finance_auditor" in guard
    assert "角色名称: SIQ 投委会财务专家" in guard
    assert "startup_retrieval_required: true" in guard
    assert "historical financials" in guard
    assert "不做行业技术判断" in guard
    assert "agents/hermes/profiles/siq_ic_finance_auditor/AGENTS.md" in guard
    assert "agents/hermes/profiles/siq_ic_finance_auditor/TOOLS.md" in guard
    assert "后端独立服务: deal_evidence、deal_retrieval" in guard
    assert "角色技能白名单: ic-finance-auditor" in guard
    assert "数据 namespace: primary_market" in guard
    assert "允许数据根: data/wiki/deals/{deal_id}" in guard
    assert "禁止数据根: data/wiki/companies" in guard
    assert "一级市场 namespace 禁止读取 data/wiki/companies" in guard
    assert "若主持人问题要求越权" in guard
    assert "assumed/待核验" in guard


def test_unknown_ic_profile_contract_raises_value_error():
    with pytest.raises(ValueError):
        ic_profile_contract.get_ic_profile_contract("siq_ic_not_real")
