from __future__ import annotations

from copy import deepcopy

import pytest
from services.ic_contract_validation import ICContractValidationError

from services import (
    ic_r4_report_renderer as renderer,
    ic_report_contracts,
    ic_report_quality as quality,
)

DEAL_ID = "DEAL-QUALITY-001"
SNAPSHOT = "b" * 64
EVIDENCE_ID = "EVID-DEAL-QUALITY-001-000001"
CLAIM_ID = "CLM-QUALITY-001"
CHAIRMAN_KB_REF = {
    "ref_id": "KBREF-CHAIR-001",
    "collection": "siq_ic_chairman",
    "locator": "milvus:siq_ic_chairman:001",
    "title": "decision discipline",
    "usage": "methodology",
}


def _gate(agent_id: str) -> dict:
    return {
        "receipt_id": f"receipt-{agent_id}",
        "allowed_to_speak": True,
        "project_evidence_ready": True,
        "private_background_ready": True,
        "shared_collection": "siq_deal_shared",
        "private_collection": agent_id,
        "blocking_reasons": [],
    }


def _claim(*, numeric: bool = True) -> dict:
    claim = {
        "claim_id": CLAIM_ID,
        "topic": "revenue_quality",
        "conclusion": "2025 revenue quality is supported by the prospectus.",
        "status": "derived",
        "evidence_ids": [EVIDENCE_ID],
        "counter_evidence_ids": [],
        "calculation_trace_ids": ["CALC-QUALITY-001"],
        "background_knowledge_ref_ids": [],
        "methodology_ref_ids": ["KBREF-CHAIR-001"],
        "confidence": "high",
        "decision_impact": "critical",
        "period": "2025",
        "currency": "CNY",
        "unit": "million",
    }
    if numeric:
        claim["value"] = 100.0
    return claim


def _dimension(index: int) -> dict:
    return {
        "dimension": f"D{index}",
        "score": 80,
        "weight": 1 / 6,
        "rationale": "Project Evidence and explicit claim support this score.",
        "claim_ids": [CLAIM_ID],
        "evidence_ids": [EVIDENCE_ID],
        "confidence": "high",
    }


def _decision() -> dict:
    return {
        "schema_version": "siq_ic_r4_decision_v2",
        "report_id": "ICRPT-R4-QUALITY-0001",
        "workflow_run_id": "ICRUN-QUALITY-00000001",
        "deal_id": DEAL_ID,
        "agent_id": "siq_ic_chairman",
        "research_identity": {"deal_id": DEAL_ID, "source_ids": ["PM:SOURCE"]},
        "evidence_snapshot_hash": SNAPSHOT,
        "recommendation": "conditional_support",
        "claims": [_claim()],
        "background_knowledge_refs": [],
        "methodology_refs": [CHAIRMAN_KB_REF],
        "startup_receipt_id": "receipt-siq_ic_chairman",
        "startup_retrieval_gate": _gate("siq_ic_chairman"),
        "six_dimension_scorecard": [_dimension(index) for index in range(1, 7)],
        "weighted_agent_score": 78,
        "chairman_dimension_score": 80,
        "chairman_qualitative_decision": "建议投资，但需满足前置条件。",
        "executive_summary": "收入质量已有项目证据支持，建议在客户穿透核验完成后推进投资。",
        "decision_rationale": "项目证据支持核心收入结论，但最新季度客户续约仍需作为交割前置条件核验。",
        "verified_facts": ["2025 年收入质量结论已绑定招股书项目证据。"],
        "assumptions": ["最新季度客户续约情况与招股书披露不存在重大不利变化。"],
        "core_disputes": ["收入可持续性需通过关键客户穿透核验关闭。"],
        "principal_risks": ["客户集中度与续约变化可能影响收入持续性。"],
        "valuation_and_exit": ["估值结论以客户穿透核验及交割保护条款为前提。"],
        "threshold_result": "pass",
        "conditions": ["完成关键客户收入穿透核验"],
        "term_sheet_protections": ["设置估值调整条款"],
        "monitoring_metrics": ["经营现金流"],
        "decision": "pass",
        "score_delta_explanation": "主席对收入质量保留审慎折价。",
        "generation_mode": "model",
        "revision": 1,
        "parent_report_id": None,
        "created_at": "2026-07-13T11:00:00+08:00",
    }


def test_r4_contract_rejects_dangling_scorecard_claim_id():
    decision = _decision()
    decision["six_dimension_scorecard"][3]["claim_ids"] = ["CLM-QUALITY-UNKNOWN"]

    with pytest.raises(
        ICContractValidationError,
        match="six_dimension_scorecard_unknown_claim_ids:CLM-QUALITY-UNKNOWN",
    ):
        ic_report_contracts.validate_r4_decision(
            decision,
            expected_deal_id=DEAL_ID,
            expected_snapshot_hash=SNAPSHOT,
            known_evidence={EVIDENCE_ID: {"deal_id": DEAL_ID}},
        )


def _finance_report() -> dict:
    finance_kb = {
        "ref_id": "KBREF-FINANCE-001",
        "collection": "siq_ic_finance_auditor",
        "locator": "milvus:siq_ic_finance_auditor:001",
        "title": "revenue quality methodology",
        "usage": "background",
    }
    claim = deepcopy(_claim())
    claim["methodology_ref_ids"] = []
    claim["background_knowledge_ref_ids"] = ["KBREF-FINANCE-001"]
    return {
        "schema_version": "siq_ic_expert_report_v2",
        "report_id": "ICRPT-FINANCE-QUALITY-0001",
        "workflow_run_id": "ICRUN-QUALITY-00000001",
        "deal_id": DEAL_ID,
        "phase": "R1A",
        "agent_id": "siq_ic_finance_auditor",
        "research_identity": {"deal_id": DEAL_ID},
        "evidence_snapshot_hash": SNAPSHOT,
        "recommendation": "conditional_support",
        "score": 80,
        "confidence": "high",
        "claims": [claim],
        "scorecard": [_dimension(1)],
        "red_flags": [],
        "open_questions": [],
        "required_followups": [],
        "executive_summary": "财务证据支持有条件推进。",
        "methodology": ["收入质量核验"],
        "background_knowledge_refs": [finance_kb],
        "methodology_refs": [],
        "startup_receipt_id": "receipt-siq_ic_finance_auditor",
        "startup_retrieval_gate": _gate("siq_ic_finance_auditor"),
        "limitations": [],
        "generation_mode": "model",
        "revision": 1,
        "parent_report_id": None,
        "created_at": "2026-07-13T10:00:00+08:00",
        "historical_financials": {"periods": ["2025"]},
        "financial_reconciliations": {"status": "pass"},
        "quality_of_earnings": {"status": "review"},
        "cash_flow_assessment": {"status": "pass"},
        "forecast_scenarios": {"base": 1},
        "valuation_scenarios": {"base": 1},
        "sensitivity_analysis": {"revenue": [-0.1, 0.1]},
        "calculation_trace_ids": ["CALC-QUALITY-001"],
    }


def _factcheck(status: str = "pass") -> dict:
    return {
        "schema_version": "siq_ic_report_factcheck_v1",
        "status": status,
        "claim_checks": [],
        "numeric_checks": [],
        "citation_checks": [],
        "contradictions": [],
        "unsupported_claims": [],
        "required_repairs": [],
        "report_id": "ICRPT-R4-QUALITY-0001",
        "report_revision": 1,
        "checked_at": "2026-07-13T11:05:00+08:00",
    }


def _bundle(decision: dict, finance_report: dict) -> dict:
    return {
        "project": {
            "company_name": "Quality Co",
            "stage": "Pre-IPO",
            "investment_structure": "增资入股",
            "company_overview": "工业软件发行人。",
            "product_overview": "核心产品为工业操作系统。",
            "business_model": "软件许可与服务收入。",
        },
        "decision": decision,
        "r0_readiness": {
            "readiness": "ready",
            "evidence_snapshot_hash": SNAPSHOT,
            "material_completeness": {"prospectus": "ready"},
            "evidence_gaps": [],
            "blocking_reasons": [],
        },
        "materials_manifest": {
            "status": "ready",
            "completeness": {"prospectus": "complete"},
            "blocking_reasons": [],
        },
        "evidence_snapshot": {
            "snapshot_hash": SNAPSHOT,
            "active_sources": [
                {
                    "source_id": "PM:SOURCE",
                    "capabilities": {
                        "text_evidence": "ready",
                        "financial_facts": "ready",
                    },
                }
            ],
        },
        "r1_reports": {finance_report["agent_id"]: finance_report},
        "r1_5_disputes": [
            {
                "dispute_id": "DSP-QUALITY-001",
                "severity": "medium",
                "question": "收入可持续性",
                "ruling": "synthesize",
                "rationale": "以客户穿透核验作为条件",
            }
        ],
        "r2_revisions": [],
        "r3_debates": [],
        "evidence_quality": {"status": "pass", "limitations": ["缺少一个季度更新"]},
        "open_questions": ["核验最新季度客户续约"],
        "human_confirmation": {"status": "pending"},
        "factcheck": _factcheck(),
        "audit_summary": "R0-R4 artifacts are revisioned.",
    }


def test_renderer_uses_one_view_model_for_complete_markdown_and_html():
    decision = _decision()
    finance = _finance_report()
    rendered = renderer.render_r4_report(_bundle(decision, finance))

    assert rendered["schema_version"] == "siq_ic_r4_rendered_report_v1"
    assert len(rendered["view_model"]["sections"]) == 15
    for title in renderer.R4_SECTION_TITLES:
        assert title in rendered["markdown"]
        assert title in rendered["html"]
    for value in ("pass", "78", "80"):
        assert value in rendered["markdown"]
        assert value in rendered["html"]
    assert "请参见其他文件" not in rendered["markdown"]


def test_renderer_prefers_latest_r2_revision_and_falls_back_to_r1():
    decision = _decision()
    finance_r1 = _finance_report()
    strategist_r1 = deepcopy(finance_r1)
    strategist_r1.update(
        {
            "agent_id": "siq_ic_strategist",
            "report_id": "ICRPT-STRATEGY-R1-0001",
            "executive_summary": "战略 R1 结论用于缺失 R2 时回退。",
        }
    )
    finance_r2_old = deepcopy(finance_r1)
    finance_r2_old.update(
        {
            "phase": "R2",
            "revision": 2,
            "report_id": "ICRPT-FINANCE-R2-0001",
            "score": 74,
            "executive_summary": "较旧的 R2 财务修订。",
            "created_at": "2026-07-13T10:30:00+08:00",
        }
    )
    finance_r2_latest = deepcopy(finance_r2_old)
    finance_r2_latest.update(
        {
            "revision": 3,
            "report_id": "ICRPT-FINANCE-R2-0002",
            "score": 72,
            "executive_summary": "最新 R2 财务修订覆盖 R1。",
            "created_at": "2026-07-13T10:45:00+08:00",
        }
    )
    bundle = _bundle(decision, finance_r1)
    bundle["r1_reports"][strategist_r1["agent_id"]] = strategist_r1
    bundle["r2_reports"] = {finance_r1["agent_id"]: finance_r2_old}
    bundle["r2_revisions"] = [
        {
            "schema_version": "siq_ic_r2_revision_v1",
            "report": finance_r2_latest,
            "r1_score": 80,
            "r2_score": 72,
            "score_change": -8,
            "revision_rationale": "新增 Evidence 下调评分。",
        }
    ]

    view = renderer.build_r4_report_view_model(bundle)
    by_title = {section["title"]: section["lines"] for section in view["sections"]}
    finance_lines = "\n".join(by_title["历史财务、收入质量、现金流、预测与估值"])
    strategy_lines = "\n".join(by_title["战略与政策分析"])

    assert "最新 R2 财务修订覆盖 R1" in finance_lines
    assert "较旧的 R2 财务修订" not in finance_lines
    assert "财务证据支持有条件推进" not in finance_lines
    assert "战略 R1 结论用于缺失 R2 时回退" in strategy_lines
    assert view["source_report_selection"]["siq_ic_finance_auditor"] == {
        "source_phase": "R2",
        "report_id": "ICRPT-FINANCE-R2-0002",
        "phase": "R2",
        "revision": 3,
    }
    assert view["source_report_selection"]["siq_ic_strategist"]["source_phase"] == "R1"


def test_renderer_uses_field_specific_claims_without_hiding_uncovered_project_fields():
    decision = _decision()
    decision.pop("term_sheet_protections")
    decision["claims"] = [
        {
            **_claim(numeric=False),
            "claim_id": "CLM-QUALITY-COMPOSITE-001",
            "topic": "company_product_competitive_position",
            "conclusion": "市场份额和客户复购表现稳定。",
        },
        {
            **_claim(numeric=False),
            "claim_id": "CLM-QUALITY-COMPANY-001",
            "topic": "company_overview",
            "status": "missing",
            "evidence_ids": [],
            "conclusion": "发行人概况仍待补证。",
        },
        {
            **_claim(numeric=False),
            "claim_id": "CLM-QUALITY-PRODUCT-001",
            "topic": "product_overview",
            "conclusion": "核心产品已完成客户验收。",
        },
        {
            **_claim(numeric=False),
            "claim_id": "CLM-QUALITY-BUSINESS-001",
            "topic": "business_model",
            "conclusion": "收入模式由设备销售与服务构成。",
        },
        {
            **_claim(numeric=False),
            "claim_id": "CLM-QUALITY-DEAL-001",
            "topic": "transaction_structure",
            "conclusion": "本轮投资结构为增资入股。",
        },
        {
            **_claim(numeric=False),
            "claim_id": "CLM-QUALITY-TS-001",
            "topic": "term_sheet_investor_protection",
            "conclusion": "投资协议设置清算优先与反稀释保护。",
        },
    ]
    bundle = _bundle(decision, _finance_report())
    for field in ("investment_structure", "company_overview", "product_overview", "business_model"):
        bundle["project"].pop(field)

    rendered = renderer.render_r4_report(bundle)

    for text in (
        "核心产品已完成客户验收。",
        "收入模式由设备销售与服务构成。",
        "本轮投资结构为增资入股。",
        "投资协议设置清算优先与反稀释保护。",
        EVIDENCE_ID,
    ):
        assert text in rendered["markdown"]
        assert text in rendered["html"]
    assert "市场份额和客户复购表现稳定。" not in rendered["markdown"]
    for placeholder in (
        "证据不足：未提供拟投资结构",
        "证据不足：未提供产品概况",
        "证据不足：未提供商业模式概况",
        "证据不足：未提供 TS 保护条款",
    ):
        assert placeholder not in rendered["markdown"]
    assert "证据不足：未提供企业概况" in rendered["markdown"]
    assert rendered["view_model"]["traceable_fallback_claim_ids"] == {
        "investment_structure": ["CLM-QUALITY-DEAL-001"],
        "company_overview": [],
        "product_overview": ["CLM-QUALITY-PRODUCT-001"],
        "business_model": ["CLM-QUALITY-BUSINESS-001"],
        "term_sheet_protections": ["CLM-QUALITY-TS-001"],
    }


def test_renderer_keeps_missing_markers_when_fallback_claims_are_not_traceable():
    decision = _decision()
    decision.pop("term_sheet_protections")
    decision["claims"] = [
        {
            **_claim(numeric=False),
            "topic": "transaction_structure_and_term_sheet",
            "status": "missing",
            "evidence_ids": [],
            "conclusion": "交易结构和投资人保护仍待补证。",
        }
    ]
    bundle = _bundle(decision, _finance_report())
    for field in ("investment_structure", "company_overview", "product_overview", "business_model"):
        bundle["project"].pop(field)

    rendered = renderer.render_r4_report(bundle)

    for placeholder in (
        "证据不足：未提供拟投资结构",
        "证据不足：未提供企业概况",
        "证据不足：未提供产品概况",
        "证据不足：未提供商业模式概况",
        "证据不足：未提供 TS 保护条款",
    ):
        assert placeholder in rendered["markdown"]
    assert rendered["view_model"]["traceable_fallback_claim_ids"] == {
        "investment_structure": [],
        "company_overview": [],
        "product_overview": [],
        "business_model": [],
        "term_sheet_protections": [],
    }


def test_renderer_includes_r0_material_and_evidence_restrictions():
    bundle = _bundle(_decision(), _finance_report())
    bundle["r0_readiness"].update(
        {
            "readiness": "needs_more_evidence",
            "evidence_gaps": ["客户合同仍待穿透"],
            "blocking_reasons": ["关键客户证据不完整"],
        }
    )
    bundle["materials_manifest"].update(
        {
            "status": "ready_with_restrictions",
            "limitations": ["招股书问询回复尚未上传"],
        }
    )
    bundle["evidence_quality"].update(
        {
            "status": "warn",
            "missing_dimensions": ["legal"],
            "limitations": ["法律证据页码待复核"],
        }
    )
    bundle["evidence_snapshot"]["active_sources"][0]["capabilities"]["financial_facts"] = "blocked"

    rendered = renderer.render_r4_report(bundle)
    context = rendered["view_model"]["r0_context"]

    assert context["readiness"] == "needs_more_evidence"
    assert context["materials_status"] == "ready_with_restrictions"
    assert context["missing_dimensions"] == ["legal"]
    for text in (
        "客户合同仍待穿透",
        "招股书问询回复尚未上传",
        "法律证据页码待复核",
        "financial_facts=blocked",
    ):
        assert text in rendered["markdown"]


def test_renderer_redacts_internal_paths_without_changing_structured_decision():
    decision = _decision()
    finance = _finance_report()
    decision["conditions"].append("read /home/maoyd/private/prompt.txt")
    rendered = renderer.render_r4_report(_bundle(decision, finance))

    assert "/home/maoyd" not in rendered["markdown"]
    assert "[内部位置已隐藏]" in rendered["markdown"]
    assert rendered["view_model"]["decision"] == "pass"


def test_quality_gate_passes_traceable_report_with_factcheck():
    decision = _decision()
    finance = _finance_report()
    rendered = renderer.render_r4_report(_bundle(decision, finance))
    evidence = {EVIDENCE_ID: {"deal_id": DEAL_ID, "source_type": "primary_market_prospectus"}}

    result = quality.evaluate_report_quality(
        decision,
        expert_reports=[finance],
        required_expert_agent_ids=["siq_ic_finance_auditor"],
        known_evidence=evidence,
        expected_deal_id=DEAL_ID,
        expected_snapshot_hash=SNAPSHOT,
        disputes=[],
        r3_plan={"mode": "full"},
        rendered_markdown=rendered["markdown"],
        rendered_html=rendered["html"],
        required_section_titles=renderer.R4_SECTION_TITLES,
        factcheck=_factcheck(),
    )

    assert result["status"] == "pass"
    assert result["allowed_for_human_confirmation"] is True
    assert result["report_revision"] == decision["revision"]
    assert result["metrics"]["background_knowledge_reference_count"] == 1


def test_quality_gate_blocks_numeric_trace_dispute_veto_and_background_evidence_misuse():
    decision = _decision()
    decision["claims"][0]["calculation_trace_ids"] = []
    finance = _finance_report()
    finance["veto_flags"] = [{"status": "open", "reason": "cash quality"}]
    rendered = renderer.render_r4_report(_bundle(decision, finance))
    evidence = {
        EVIDENCE_ID: {
            "deal_id": DEAL_ID,
            "source_type": "background_knowledge",
        }
    }

    result = quality.evaluate_report_quality(
        decision,
        expert_reports=[finance],
        required_expert_agent_ids=["siq_ic_finance_auditor"],
        known_evidence=evidence,
        expected_deal_id=DEAL_ID,
        expected_snapshot_hash=SNAPSHOT,
        disputes=[{"dispute_id": "DSP-BLOCK", "severity": "critical", "ruling": "unresolved"}],
        r3_plan={"mode": "skip", "skip_checks": {"critical_closed": False}},
        rendered_markdown=rendered["markdown"],
        rendered_html=rendered["html"],
        required_section_titles=renderer.R4_SECTION_TITLES,
        factcheck=_factcheck(),
    )

    assert result["status"] == "fail"
    assert result["allowed_for_human_confirmation"] is False
    assert {
        "schema.r4",
        "evidence.identity",
        "financial.numeric_trace",
        "disputes.resolution",
        "red_flags.veto",
        "r3.skip_safety",
    }.issubset(set(result["blocking_reasons"]))


def test_numeric_trace_distinguishes_verified_derived_monetary_and_missing_claims():
    verified_percent = {
        "decision_impact": "material",
        "status": "verified",
        "value": 48,
        "period": "2025",
        "unit": "percent",
        "currency": None,
        "evidence_ids": [EVIDENCE_ID],
        "calculation_trace_ids": [],
    }
    verified_money = {
        **verified_percent,
        "unit": "million_yuan",
        "currency": None,
    }
    derived = {
        **verified_percent,
        "status": "derived",
    }
    missing = {
        **verified_percent,
        "status": "missing",
        "period": None,
        "unit": None,
        "evidence_ids": [],
    }

    assert quality._numeric_trace_missing_fields(verified_percent) == []
    assert quality._numeric_trace_missing_fields(verified_money) == ["currency"]
    assert quality._numeric_trace_missing_fields(derived) == ["calculation_trace_ids"]
    assert quality._numeric_trace_missing_fields(missing) == []


def test_factcheck_authoring_contract_excludes_server_fields_and_unbounded_objects():
    schema = quality.factcheck_authoring_schema()
    assert schema["x-projection"] == "server_managed_fields_omitted"
    for field in quality.FACTCHECK_SERVER_MANAGED_FIELDS:
        assert field not in schema["properties"]

    authored = _factcheck("warn")
    for field in quality.FACTCHECK_SERVER_MANAGED_FIELDS:
        authored.pop(field, None)
    authored["claim_checks"] = [
        {
            "claim_id": CLAIM_ID,
            "status": "warn",
            "message": "The report needs a narrower caveat.",
            "evidence_ids": [EVIDENCE_ID],
        }
    ]
    assert quality.validate_factcheck_authoring_result(authored) == authored

    with pytest.raises(ValueError, match="model-authoring-payload contract invalid"):
        quality.validate_factcheck_authoring_result({**authored, "report_id": "ICRPT-ILLEGAL"})
    with pytest.raises(ValueError, match="model-authoring-payload contract invalid"):
        quality.validate_factcheck_authoring_result(
            {**authored, "claim_checks": [{"claim_id": CLAIM_ID, "calc": "1+1"}]}
        )


def test_factcheck_authoring_allows_report_wide_evidence_refs_with_a_finite_bound():
    authored = _factcheck("warn")
    for field in quality.FACTCHECK_SERVER_MANAGED_FIELDS:
        authored.pop(field, None)
    evidence_ids = [f"EVID-DEAL-QUALITY-001-{index:06d}" for index in range(22)]
    authored["numeric_checks"] = [
        {
            "check_id": "CC-NUM-WEIGHT",
            "status": "verified",
            "message": "The report-wide weighted score was recomputed.",
            "evidence_ids": evidence_ids,
        }
    ]

    assert quality.validate_factcheck_authoring_result(authored) == authored

    authored["numeric_checks"][0]["evidence_ids"] = [
        f"EVID-DEAL-QUALITY-001-{index:06d}"
        for index in range(quality.FACTCHECK_MAX_EVIDENCE_IDS_PER_FINDING + 1)
    ]
    with pytest.raises(ValueError, match="model-authoring-payload contract invalid"):
        quality.validate_factcheck_authoring_result(authored)


def test_factcheck_critical_unsupported_claim_blocks_and_cannot_silently_repair():
    decision = _decision()
    finance = _finance_report()
    rendered = renderer.render_r4_report(_bundle(decision, finance))
    factcheck = _factcheck("fail")
    factcheck["unsupported_claims"] = [{"claim_id": CLAIM_ID, "severity": "critical"}]
    factcheck["required_repairs"] = [{"claim_id": CLAIM_ID, "action": "add evidence"}]

    result = quality.evaluate_report_quality(
        decision,
        expert_reports=[finance],
        required_expert_agent_ids=["siq_ic_finance_auditor"],
        known_evidence={EVIDENCE_ID: {"deal_id": DEAL_ID}},
        rendered_markdown=rendered["markdown"],
        rendered_html=rendered["html"],
        factcheck=factcheck,
    )
    assert "factcheck.result" in result["blocking_reasons"]

    original = deepcopy(decision)
    repaired = deepcopy(decision)
    repaired["report_id"] = "ICRPT-R4-QUALITY-0002"
    repaired["parent_report_id"] = original["report_id"]
    repaired["revision"] = 2
    revision = quality.build_repair_revision(
        original,
        repaired,
        factcheck=factcheck,
        repair_summary=["Added project Evidence for the critical claim."],
        revised_by={"id": 7, "username": "reviewer"},
    )
    assert revision["parent_report_id"] == original["report_id"]
    assert revision["report_id"] != original["report_id"]
    assert original["revision"] == 1

    silent = deepcopy(original)
    with pytest.raises(ICContractValidationError, match="new_report_id"):
        quality.build_repair_revision(
            original,
            silent,
            factcheck=factcheck,
            repair_summary=["silent mutation"],
            revised_by={"id": 7},
        )
