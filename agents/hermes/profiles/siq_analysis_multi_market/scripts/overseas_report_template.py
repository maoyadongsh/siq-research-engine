"""Formal report contract for non-CN annual-report analysis."""

from __future__ import annotations

from typing import Any, Mapping

TEMPLATE_ID = "siq_overseas_annual_report_v1"
TEMPLATE_VERSION = "1.0.0"
SUPPORTED_MARKETS = frozenset({"HK", "US", "EU", "KR", "JP"})

SECTION_SPECS = (
    ("executive_summary", "投资结论与关键变化", 6),
    ("business_overview", "商业模式、披露口径与竞争定位", 5),
    ("revenue_quality", "收入增长、结构与可持续性", 5),
    ("profitability", "盈利能力、利润驱动与收益质量", 5),
    ("balance_sheet", "资产负债、流动性与偿债安全", 5),
    ("cash_flow", "现金流、营运资本与自由现金流", 5),
    ("capital_allocation", "资本配置、股东回报与再投资", 4),
    ("segments", "分部、地域与经营驱动", 4),
    ("risk_factors", "关键风险、传导路径与压力信号", 5),
    ("controls", "治理、审计与内部控制", 4),
    ("accounting_quality", "会计政策、非经常项目与报表质量", 5),
    ("valuation_boundary", "估值框架、预期差与数据边界", 4),
    ("tracking", "催化剂、反证条件与跟踪清单", 6),
    ("traceability", "数据质量、证据覆盖与分析限制", 4),
)

REQUIRED_ANALYSIS_DIMENSIONS = (
    "multi_period_trend",
    "business_driver",
    "cross_statement_check",
    "earnings_quality",
    "cash_conversion",
    "balance_sheet_resilience",
    "capital_allocation",
    "segment_or_geography",
    "governance_and_audit",
    "valuation_boundary",
    "catalyst_and_risk_signal",
    "evidence_and_limitations",
)

SECTION_ANALYSIS_DIMENSIONS = {
    "executive_summary": ("multi_period_trend", "cross_statement_check", "evidence_and_limitations"),
    "business_overview": ("business_driver", "segment_or_geography"),
    "revenue_quality": ("multi_period_trend", "business_driver", "cash_conversion"),
    "profitability": ("business_driver", "cross_statement_check", "earnings_quality"),
    "balance_sheet": ("balance_sheet_resilience", "cross_statement_check"),
    "cash_flow": ("cash_conversion", "cross_statement_check"),
    "capital_allocation": ("capital_allocation", "balance_sheet_resilience"),
    "segments": ("segment_or_geography", "business_driver"),
    "risk_factors": ("catalyst_and_risk_signal", "balance_sheet_resilience"),
    "controls": ("governance_and_audit",),
    "accounting_quality": ("earnings_quality", "governance_and_audit"),
    "valuation_boundary": ("valuation_boundary", "catalyst_and_risk_signal"),
    "tracking": ("catalyst_and_risk_signal", "multi_period_trend"),
    "traceability": ("evidence_and_limitations",),
}

_MARKET_ADAPTATIONS: dict[str, tuple[str, ...]] = {
    "HK": (
        "按 HKFRS/IFRS、港交所年报及管理层讨论与分析口径解释数据，不以上市地点推定呈报币种或经营地域。",
        "重点核对关连交易、控股股东治理、分部与地域披露、股息政策及多币种风险。",
    ),
    "US": (
        "按 US GAAP、Form 10-K、MD&A、Risk Factors、审计意见及 iXBRL context 解释数据。",
        "明确区分 GAAP 与 non-GAAP、股份回购与股权激励、税务不确定性及网络安全等披露。",
    ),
    "EU": (
        "按 IFRS、管理报告、可持续披露与所在司法辖区监管语境解释数据。",
        "重点核对替代业绩指标、养老金、能源与汇率风险、资本结构及地域分部。",
    ),
    "JP": (
        "按 IFRS/J-GAAP/US GAAP 的实际披露准则解释数据，不以日本上市身份预设会计口径。",
        "重点核对交叉持股、政策性持股、资本效率、日元敏感性、治理改革与股东回报。",
    ),
    "KR": (
        "按 K-IFRS、韩国定期报告及合并口径解释数据。",
        "重点核对集团关联交易、外汇与出口周期、少数股东权益、资本开支及治理结构。",
    ),
}


def build_template_contract(
    market: str,
    *,
    report_type: str | None = None,
    accounting_standard: str | None = None,
    entity_kind: str | None = None,
    entity_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_market = str(market or "").strip().upper()
    if normalized_market not in SUPPORTED_MARKETS:
        raise ValueError(f"unsupported overseas report market: {normalized_market or 'unknown'}")
    profile = entity_profile if isinstance(entity_profile, Mapping) else {}
    resolved_entity_kind = str(entity_kind or profile.get("kind") or "general")
    return {
        "template_id": TEMPLATE_ID,
        "template_version": TEMPLATE_VERSION,
        "scope": "non_cn_listed_company_annual_report",
        "market": normalized_market,
        "report_type": str(report_type or "unknown"),
        "accounting_standard": str(accounting_standard or "unknown"),
        "entity_kind": resolved_entity_kind,
        "section_ids": [section_id for section_id, _, _ in SECTION_SPECS],
        "section_minimum_items": {
            section_id: minimum_items for section_id, _, minimum_items in SECTION_SPECS
        },
        "section_minimum_company_items": {
            section_id: 0 if section_id == "valuation_boundary" else 1
            for section_id, _, _ in SECTION_SPECS
        },
        "section_analysis_dimensions": {
            section_id: list(SECTION_ANALYSIS_DIMENSIONS[section_id])
            for section_id, _, _ in SECTION_SPECS
        },
        "required_analysis_dimensions": list(REQUIRED_ANALYSIS_DIMENSIONS),
        "market_adaptations": list(_MARKET_ADAPTATIONS[normalized_market]),
        "evidence_policy": {
            "company_specific_claims_require_evidence": True,
            "numbers_require_period_unit_currency_and_locator": True,
            "inference_must_be_labeled": True,
            "missing_data_must_degrade_not_impute": True,
        },
        "presentation_policy": {
            "evidence_catalog_default_collapsed": True,
            "evidence_catalog_bounded": True,
            "full_audit_payload_in_json": True,
        },
    }


def validate_template_contract(contract: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    if contract.get("template_id") != TEMPLATE_ID:
        failures.append("overseas_template_id_invalid")
    if contract.get("template_version") != TEMPLATE_VERSION:
        failures.append("overseas_template_version_invalid")
    if str(contract.get("market") or "") not in SUPPORTED_MARKETS:
        failures.append("overseas_template_market_invalid")
    if list(contract.get("section_ids") or ()) != [item[0] for item in SECTION_SPECS]:
        failures.append("overseas_template_section_order_invalid")
    missing_dimensions = set(REQUIRED_ANALYSIS_DIMENSIONS) - set(
        str(item) for item in contract.get("required_analysis_dimensions") or ()
    )
    if missing_dimensions:
        failures.append("overseas_template_dimensions_incomplete")
    covered_dimensions = {
        str(dimension)
        for dimensions in (contract.get("section_analysis_dimensions") or {}).values()
        for dimension in dimensions
    }
    if set(REQUIRED_ANALYSIS_DIMENSIONS) - covered_dimensions:
        failures.append("overseas_template_dimension_mapping_incomplete")
    if len(contract.get("market_adaptations") or ()) < 2:
        failures.append("overseas_template_market_adaptation_incomplete")
    return failures


__all__ = [
    "REQUIRED_ANALYSIS_DIMENSIONS",
    "SECTION_SPECS",
    "SUPPORTED_MARKETS",
    "TEMPLATE_ID",
    "TEMPLATE_VERSION",
    "build_template_contract",
    "validate_template_contract",
]
