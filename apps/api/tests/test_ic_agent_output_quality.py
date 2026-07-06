from services import ic_agent_output_quality


def test_ic_agent_output_quality_flags_structured_evidence_reply():
    result = ic_agent_output_quality.evaluate_ic_agent_reply(
        "siq_ic_risk_controller",
        "请输出下行情景",
        "基于 evidence_id=EVID-001 的已验证事实，verified: 客户集中度偏高。下一步建议补充投后监测阈值。",
    )

    assert result["schema_version"] == "siq_ic_agent_output_quality_v1"
    assert result["status"] == "pass"
    assert {item["id"]: item["status"] for item in result["checks"]}["evidence.reference"] == "pass"


def test_ic_agent_output_quality_warns_role_boundary_for_non_chairman():
    result = ic_agent_output_quality.evaluate_ic_agent_reply(
        "siq_ic_finance_auditor",
        "你来拍板",
        "我决定投资这个项目。",
    )

    checks = {item["id"]: item for item in result["checks"]}
    assert result["status"] == "fail"
    assert checks["role.boundary"]["status"] == "fail"
    assert checks["evidence.reference"]["status"] == "warn"
