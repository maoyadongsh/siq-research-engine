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


def test_ic_agent_output_quality_rejects_private_background_as_project_evidence_without_deal_evidence():
    result = ic_agent_output_quality.evaluate_ic_agent_reply(
        "siq_ic_legal_scanner",
        "合规清单",
        "当前合规指数 88 分。私有证据 4 份支持本项目风险评级：高。下一步建议补材料。",
        context={
            "startup_receipt": {
                "present": True,
                "shared_hits": 0,
                "shared_vector_hit_count": 0,
                "private_hits": 4,
            }
        },
    )

    checks = {item["id"]: item for item in result["checks"]}
    assert result["status"] == "fail"
    assert checks["evidence.private_background_boundary"]["status"] == "fail"
    assert checks["evidence.project_rating_support"]["status"] == "fail"


def test_ic_agent_output_quality_allows_checklist_without_project_rating_when_evidence_is_empty():
    result = ic_agent_output_quality.evaluate_ic_agent_reply(
        "siq_ic_legal_scanner",
        "合规清单",
        "当前尚无项目底稿。以下为法律核验框架：核查权属、资质、合同与诉讼；结论均待核验。下一步请补充材料。",
        context={
            "startup_receipt": {
                "present": True,
                "shared_hits": 0,
                "shared_vector_hit_count": 0,
                "private_hits": 4,
            }
        },
    )

    checks = {item["id"]: item for item in result["checks"]}
    assert checks["evidence.private_background_boundary"]["status"] == "pass"
    assert checks["evidence.project_rating_support"]["status"] == "pass"
