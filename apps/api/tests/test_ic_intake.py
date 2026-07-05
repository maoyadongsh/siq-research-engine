import json

from services import deal_store
from services import ic_intake


def test_r0_intake_defaults_to_local_metadata_only_and_writes_artifacts(tmp_path, monkeypatch):
    def fail_external_call(**kwargs):
        raise AssertionError("external research should not run in disabled mode")

    monkeypatch.setattr(ic_intake.external_research_clients, "run_external_research", fail_external_call)
    deal_store.create_deal_package(
        deal_id="DEAL-INTAKE-001",
        company_name="Router Robotics",
        industry="Robotics",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )

    result = ic_intake.run_r0_intake(
        "DEAL-INTAKE-001",
        created_by={"id": 7, "username": "analyst", "email": "hidden@example.com"},
        wiki_root=tmp_path,
    )

    package_dir = tmp_path / "deals" / "DEAL-INTAKE-001"
    assert result["schema_version"] == "siq_ic_r0_intake_v1"
    assert result["verification_mode"] == "local_metadata_only"
    assert result["scorecard"]["action"] == "PROCEED_WITH_CAUTION"
    assert "external_checks_disabled" in result["coverage_gaps"]
    assert result["written"] is True
    assert (package_dir / "phases" / "r0_intake.json").is_file()
    assert (package_dir / "discussion" / "00_项目信息_R0.md").is_file()
    workflow = deal_store.read_json(package_dir / "phases" / "workflow_state.json", {})
    assert workflow["phases"]["R0"]["status"] == "review_required"
    assert "hidden@example.com" not in json.dumps(deal_store.redact_public_payload(result), ensure_ascii=False)
    audit = deal_store.read_json(package_dir / "audit" / "audit_log.json", {})
    assert audit["events"][-1]["event_type"] == "deal_r0_intake_generated"


def test_r0_intake_uses_external_checks_without_leaking_config(tmp_path, monkeypatch):
    calls = []

    def fake_external_call(*, query, providers=None, max_results=5, enabled=False, timeout=10.0):
        calls.append({"query": query, "providers": providers, "enabled": enabled, "timeout": timeout})
        provider = providers[0] if providers else "unknown"
        if provider == "qcc":
            snippet = json.dumps(
                {
                    "企业名称": "杭州宇树科技股份有限公司",
                    "法定代表人": "王兴兴",
                    "注册资本": "100万元",
                    "成立日期": "2016-08-26",
                    "登记状态": "存续",
                },
                ensure_ascii=False,
            )
            results = [{"source_id": "EXT-qcc-001", "provider": "qcc", "title": query, "snippet": snippet, "url": ""}]
        else:
            results = [
                {
                    "source_id": f"EXT-{provider}-001",
                    "provider": provider,
                    "title": "Unitree Robotics financing and products",
                    "snippet": "Unitree Robotics builds humanoid robots and reported financing news.",
                    "url": "https://example.test/unitree",
                }
            ]
        return {
            "schema_version": "siq_external_research_v1",
            "enabled": enabled,
            "query": query,
            "providers": [{"provider": provider, "status": "completed", "reason": None}],
            "results": results,
            "result_count": len(results),
        }

    monkeypatch.setattr(ic_intake.external_research_clients, "run_external_research", fake_external_call)
    deal_store.create_deal_package(
        deal_id="DEAL-INTAKE-002",
        company_name="杭州宇树科技股份有限公司",
        industry="机器人",
        stage="Pre-IPO",
        wiki_root=tmp_path,
    )

    result = ic_intake.run_r0_intake(
        "DEAL-INTAKE-002",
        task_description={"main_business": "humanoid robots", "founder": "王兴兴"},
        include_external=True,
        external_providers=["qcc", "tavily"],
        wiki_root=tmp_path,
    )

    assert result["verification_mode"] == "external_cross_check"
    assert result["qcc_fields"]["company_name"] == "杭州宇树科技股份有限公司"
    assert result["qcc_fields"]["legal_rep"] == "王兴兴"
    assert result["public_facts"]["source_count"] > 0
    assert result["scorecard"]["action"] in {"PROCEED", "PROCEED_WITH_CAUTION"}
    assert all("provider_request_failed" not in gap for gap in result["coverage_gaps"])
    assert calls[0]["providers"] == ["qcc"]
    assert "https://example.test/unitree" in json.dumps(result, ensure_ascii=False)
