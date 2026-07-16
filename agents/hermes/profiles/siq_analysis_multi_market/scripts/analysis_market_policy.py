"""Deterministic Chinese analysis policy for the six supported markets.

The policy returned by this module is analysis guidance, not a collection of
company facts.  Company-specific observations are only emitted when an input
field identifies their source.  In particular, English section excerpts are
used only for coarse topic recognition; their prose is never translated into
an assertion about the issuer.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

POLICY_SCHEMA_VERSION = "siq_analysis_market_policy_v1"
SUPPORTED_MARKETS = ("CN", "HK", "US", "EU", "KR", "JP")

CHAPTER_IDS = (
    "business_overview",
    "risk_factors",
    "controls",
    "accounting_quality",
    "tracking",
)

_MARKET_ALIASES = {
    "A": "CN",
    "A_SHARE": "CN",
    "A-SHARE": "CN",
    "CHINA": "CN",
    "MAINLAND": "CN",
    "MAINLAND_CHINA": "CN",
    "HONG_KONG": "HK",
    "HONGKONG": "HK",
    "USA": "US",
    "UNITED_STATES": "US",
    "EUROPE": "EU",
    "EUROPEAN_UNION": "EU",
    "KOREA": "KR",
    "SOUTH_KOREA": "KR",
    "JAPAN": "JP",
}


_MARKET_POLICIES: dict[str, dict[str, Any]] = {
    "CN": {
        "label": "中国内地市场",
        "reporting_context": "企业会计准则与境内定期报告语境",
        "usual_standards": ("CAS", "PRC_GAAP", "企业会计准则", "CHINESE_ACCOUNTING_STANDARDS"),
        "chapters": {
            "business_overview": (
                "业务分析应交叉核对境内定期报告中的公司业务概要、经营情况讨论与分析及分部披露；只有当前报告存在章节或指标证据时，才描述产品、客户、区域或产能结构。",
                "应区分合并与母公司口径、主营与其他业务以及经常性与非经常性项目；不能把证券简称、行业标签或历史报告内容当作本期经营事实。",
            ),
            "risk_factors": (
                "风险分析优先使用当前报告的重大风险提示、经营情况讨论、审计信息和财务检查结果；风险条目缺少定位时，只能列为待核问题。",
                "监管、行业竞争、信用、流动性和减值等风险应分别说明披露来源与财务影响路径；不得仅凭市场常识断言公司已经发生相关风险。",
            ),
            "controls": (
                "内部控制结论仅能来自当前报告中的内部控制评价、内部控制审计或治理章节；未纳入当前解析包的独立内控报告不得推定为已核验。",
                "应区分制度描述、管理层自评、审计意见和整改进展；任何一类材料缺失都不能由另一类材料替代。",
            ),
            "accounting_quality": (
                "会计分析以源报告明确标注的准则为准，并核对合并范围、会计政策变更、估计变更及前期差错；市场政策不能覆盖源报告字段。",
                "金额、比例、每股指标和人数等单位必须按指标语义分别处理；同一指标只有在期间、币种、范围和会计基础一致时才计算变化。",
            ),
            "tracking": (
                "后续跟踪应绑定同一公司身份，优先比较后续年度、半年度或季度报告中的同口径指标，并保留报告期、合并范围和单位。",
                "跟踪清单应覆盖经营驱动、现金流、资产负债、减值及已披露风险的后续变化；尚无新报告或外部数据时应明确标为未执行。",
            ),
        },
    },
    "HK": {
        "label": "香港市场",
        "reporting_context": "港交所定期报告与 HKFRS/IFRS 语境",
        "usual_standards": ("HKFRS", "HK_IFRS", "IFRS"),
        "chapters": {
            "business_overview": (
                "业务分析优先使用港交所年报或中期报告中的业务回顾、管理层讨论与分析及分部附注；只有当前报告有明确定位时才描述地域、客户或业务线贡献。",
                "上市地点不能决定呈报币种或经营地域；港币、美元及其他币种必须以源报告字段和指标单位为准，多币种项目不得直接汇总。",
            ),
            "risk_factors": (
                "风险分析应以当前报告的主要风险、风险因素、管理层讨论及金融工具附注为来源；章节缺失不表示相关风险不存在。",
                "利率、汇率、信用、流动性和保险风险等表述必须绑定具体章节；不得从 HKFRS/IFRS 标签或港交所上市状态反推公司风险暴露。",
            ),
            "controls": (
                "内部控制与治理分析只陈述当前报告明确披露的董事会审阅、风险管理、内部控制或审计委员会信息，不推定其符合任何守则条文。",
                "应区分治理流程描述、有效性判断、缺陷表述和整改动作；没有对应证据时，不输出有效、无缺陷或已整改等结论。",
            ),
            "accounting_quality": (
                "源报告标注 HKFRS 或 IFRS 时，应沿用其确认计量和列报口径；若准则字段未标注，只能说明当前解析包未提供准则信息。",
                "金融机构应优先使用净利息收入、保险服务收入、监管资本或偿付能力等适用口径，不能把子项目误标为集团总收入或净利润。",
            ),
            "tracking": (
                "后续跟踪应比较同一发行人的后续年报或中期报告，并保持币种、合并范围、持续经营业务及会计准则口径一致。",
                "跟踪信号应覆盖分部变化、资本与流动性、减值及报告已披露的主要风险；未接入公告或行情数据时必须明确标为未执行或不可用。",
            ),
        },
    },
    "US": {
        "label": "美国市场",
        "reporting_context": "SEC 10-K/10-Q 与 US GAAP 语境",
        "usual_standards": ("US_GAAP", "US-GAAP", "US GAAP"),
        "chapters": {
            "business_overview": (
                "10-K 业务分析应联合使用 Business、MD&A、财务报表及附注；10-Q 主要用于识别本季度或年初至今变化，不能被当作完整年度业务重述。",
                "section_catalog 摘录只用于识别原文涉及的主题；产品、客户、竞争、分部或战略等公司事实仍须由对应章节定位或结构化证据支持。",
            ),
            "risk_factors": (
                "风险分析优先使用 Risk Factors、Market Risk 和 MD&A；10-Q 未重复某项年度风险时，不能据此判断该风险已经消失。",
                "网络安全、监管、供应链、诉讼、利率、汇率等主题只表示原文涉及相应话题；风险是否发生、程度及财务影响必须另有直接证据。",
            ),
            "controls": (
                "控制分析应优先核对 Controls and Procedures，并区分披露控制、财务报告内部控制、审计师鉴证及整改信息。",
                "包含 material weakness 或 remediation 等关键词的摘录只能标记为相关主题，不能脱离上下文断言存在重大缺陷或整改已经完成。",
            ),
            "accounting_quality": (
                "US GAAP 指标、公司扩展 XBRL 概念和 non-GAAP 指标必须分开；公司扩展概念不能自动视为 non-GAAP，同名概念也不能跨 context 合并。",
                "10-Q 应区分季度数与年初至今数，10-K 应区分时点与期间数；只有 context、dimensions、币种和会计基础一致时才进行趋势比较。",
            ),
            "tracking": (
                "后续跟踪优先连接同一发行人的后续 10-K 或 10-Q，并按 accession、期间、context 和 XBRL dimensions 保持研究身份一致。",
                "应持续观察 Business、Risk Factors、MD&A、财务报表、附注和 Controls 中已披露主题的变化；没有后续 filing 时不生成稳定或改善结论。",
            ),
        },
    },
    "EU": {
        "label": "欧洲市场",
        "reporting_context": "IFRS 年度/中期报告与 ESEF 语境",
        "usual_standards": ("IFRS", "IFRS_EU", "EU_IFRS"),
        "chapters": {
            "business_overview": (
                "业务分析应联合使用年度或中期报告中的管理报告、业务回顾、分部信息和 IFRS 财务报表附注；ESEF 标签本身不能替代业务叙述证据。",
                "跨国发行人的经营地域、功能货币与呈报货币必须分别识别；不能因上市市场为欧洲而推定收入均以欧元计量。",
            ),
            "risk_factors": (
                "风险分析优先使用管理报告、主要风险、金融风险及持续经营相关披露；只有当前解析包中的定位可以支持公司特定结论。",
                "气候、能源、汇率、利率、信用及供应链等主题必须保留原章节角色；ESEF 技术标签不能证明风险程度或管理措施有效。",
            ),
            "controls": (
                "治理与控制分析仅基于当前报告中的治理声明、风险管理、审计委员会或内部控制披露；不同司法辖区的报告要求不得相互套用。",
                "ESEF 文件通过结构化校验只说明技术产物可处理，不等同于财务报告内部控制有效，也不替代审计意见。",
            ),
            "accounting_quality": (
                "会计分析以源报告声明的 IFRS 采用口径和具体政策为准，并关注分部、减值、公允价值、租赁及收入确认等附注定位。",
                "ESEF 数值应核对 scale、decimals、unit、context 和 dimensions；展示单位换算必须保留原始值与换算依据。",
            ),
            "tracking": (
                "后续跟踪应连接同一发行人的年度或中期报告，并保持 IFRS 口径、ESEF context、呈报货币和合并范围一致。",
                "跟踪清单应覆盖分部经营、现金与融资、减值、公允价值及已披露主要风险；缺少后续结构化包时明确标为不可比较。",
            ),
        },
    },
    "KR": {
        "label": "韩国市场",
        "reporting_context": "K-IFRS 与韩国定期报告语境",
        "usual_standards": ("K_IFRS", "K-IFRS", "KIFRS", "IFRS"),
        "chapters": {
            "business_overview": (
                "业务分析应以当前韩国定期报告中的业务内容、管理层说明、分部和关联附注为边界；仅有公司或行业标签时不补写产品、客户或市场份额。",
                "韩元呈报并不代表所有业务均以韩元结算；海外分部、外币项目和换算影响必须按报告披露分别处理。",
            ),
            "risk_factors": (
                "风险分析优先使用当前报告的风险、经营说明和金融工具附注，区分行业波动、汇率、利率、信用、流动性及供应链主题。",
                "K-IFRS 标签只能界定会计语境，不能证明公司存在某项风险；风险发生与影响程度必须有当前报告直接证据。",
            ),
            "controls": (
                "内部会计管理与治理结论仅在当前解析包含相应报告、审计或治理章节时陈述；缺失时保留为待核事项。",
                "应区分管理层制度说明、运行评价、审计结论和整改状态；结构化财务数据通过校验不等同于控制有效。",
            ),
            "accounting_quality": (
                "会计分析按源报告声明的 K-IFRS 或其他准则字段执行，并核对合并范围、关联方、外币折算、减值和公允价值附注。",
                "韩元金额常见较大数量级，展示缩放必须读取原 unit 与 scale；不得把百万韩元、千韩元或原币值按固定模板换算。",
            ),
            "tracking": (
                "后续跟踪应比较同一发行人的后续定期报告，并保持 K-IFRS、合并范围、韩元单位及报告期间一致。",
                "跟踪信号应覆盖分部需求、出口与汇率、现金流、负债和已披露风险；外部公告或行情未执行时应明确说明。",
            ),
        },
    },
    "JP": {
        "label": "日本市场",
        "reporting_context": "日本财年与 J-GAAP/IFRS 定期报告语境",
        "usual_standards": ("J_GAAP", "J-GAAP", "JGAAP", "IFRS"),
        "chapters": {
            "business_overview": (
                "业务分析应以当前日本定期报告中的业务概况、经营讨论、分部和财务附注为边界，并按公司实际财年截止日而非自然年归属期间。",
                "日元呈报、海外业务和外币换算应分别识别；上市地点或公司国籍不能替代源报告的呈报币种及经营地域证据。",
            ),
            "risk_factors": (
                "风险分析优先使用当前报告的业务风险、经营讨论和金融工具附注；只有章节主题而无直接表述时，不判断风险已经发生。",
                "汇率、供应链、自然灾害、客户集中或资产减值等主题必须绑定当前报告证据；不得从日本市场共性推断个别公司暴露。",
            ),
            "controls": (
                "内部控制结论仅在当前包包含相应治理、内部控制或审计材料时陈述；独立内部控制报告未解析时应明确资料边界。",
                "应区分制度说明、评价范围、发现事项和整改进展；财务报表采用 J-GAAP 或 IFRS 并不自动证明控制有效。",
            ),
            "accounting_quality": (
                "会计分析以源报告标注的 J-GAAP 或 IFRS 为准，不能因市场归属预设准则；准则转换或会计政策变化须有附注证据。",
                "比较期间应按发行人财年、季度或累计期间对齐，并保持币种、单位、合并范围及持续经营业务口径一致。",
            ),
            "tracking": (
                "后续跟踪应连接同一发行人的后续定期报告，按实际财年截止日对齐 J-GAAP 或 IFRS 指标及合并范围。",
                "跟踪清单应覆盖分部、海外收入、汇率、现金流、资产负债和已披露风险；没有可比指标时应报告数据不足，且不得给出趋势判断。",
            ),
        },
    },
}


# The matching result is deliberately a topic label, never a translation of
# the excerpt.  Negated phrases therefore remain safe (for example, "no
# material weakness" still maps only to "重大缺陷相关表述").
_TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("产品与服务相关表述", ("products and services", "our products", "our services", "product portfolio")),
    (
        "客户、渠道或客户集中度相关表述",
        ("customers", "customer concentration", "distribution channels", "sales channels"),
    ),
    ("竞争与市场环境相关表述", ("competition", "competitive", "market environment", "market position")),
    ("分部与地域经营相关表述", ("reportable segment", "operating segment", "geographic", "segment information")),
    ("经营结果与趋势相关表述", ("results of operations", "known trends", "operating results", "year over year")),
    ("流动性与资本资源相关表述", ("liquidity and capital resources", "liquidity", "capital resources")),
    (
        "关键会计估计相关表述",
        ("critical accounting estimates", "critical accounting policies", "significant estimates"),
    ),
    ("风险因素相关表述", ("risk factors", "principal risks", "material risks")),
    ("网络安全或数据安全相关表述", ("cybersecurity", "cyber security", "data security", "privacy")),
    ("供应链相关表述", ("supply chain", "suppliers", "raw materials")),
    ("监管与合规相关表述", ("regulation", "regulatory", "compliance")),
    ("诉讼或法律程序相关表述", ("legal proceedings", "litigation", "lawsuits")),
    (
        "利率、汇率或市场风险相关表述",
        ("interest rate risk", "foreign currency risk", "foreign exchange risk", "market risk"),
    ),
    ("信用或流动性风险相关表述", ("credit risk", "liquidity risk")),
    ("披露控制相关表述", ("disclosure controls", "disclosure controls and procedures")),
    ("财务报告内部控制相关表述", ("internal control over financial reporting", "icfr")),
    ("重大缺陷相关表述", ("material weakness", "material weaknesses")),
    ("控制整改相关表述", ("remediation", "remediate", "remedial actions")),
    ("审计委员会相关表述", ("audit committee",)),
    ("收入确认相关表述", ("revenue recognition", "revenue from contracts with customers")),
    ("减值相关表述", ("impairment", "expected credit losses", "credit losses")),
    ("公允价值相关表述", ("fair value",)),
    ("租赁相关表述", ("leases", "lease liabilities")),
)

_ROLE_TO_CHAPTER = {
    "business": "business_overview",
    "mda": "business_overview",
    "segments": "business_overview",
    "risk_factors": "risk_factors",
    "market_risk": "risk_factors",
    "controls": "controls",
    "notes": "accounting_quality",
    "financial_statements": "accounting_quality",
}

_EXPECTED_US_ROLES = ("business", "risk_factors", "mda", "controls", "notes")


def build_analysis_market_policy(
    market: str,
    source_report: Mapping[str, Any] | None = None,
    source_metadata: Mapping[str, Any] | None = None,
    financial_checks: Mapping[str, Any] | None = None,
    entity_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build reusable, evidence-bounded Chinese guidance for one market.

    The function is pure and deterministic.  It never reads files, performs
    network access, or copies an excerpt into report prose.
    """

    market_code = normalize_market_code(market)
    config = _MARKET_POLICIES.get(market_code)
    unsupported = config is None
    if config is None:
        config = _fallback_policy(market_code)

    report = dict(source_report or {})
    metadata = dict(source_metadata or {})
    profile = dict(entity_profile or {})
    checks = _financial_checks_payload(financial_checks, metadata)
    source_topics, topic_meta = _recognize_section_topics(metadata.get("section_catalog"))

    sections: dict[str, list[dict[str, Any]]] = {}
    for chapter_id in CHAPTER_IDS:
        sections[chapter_id] = [
            _insight(
                market_code,
                chapter_id,
                index,
                text,
                basis="market_policy",
                scope="analysis_rule",
            )
            for index, text in enumerate(config["chapters"][chapter_id], 1)
        ]

    declared_form = _compact(
        report.get("form_type") or report.get("report_type") or report.get("fiscal_period"),
        limit=48,
    )
    if declared_form:
        sections["business_overview"].append(
            _insight(
                market_code,
                "business_overview",
                len(sections["business_overview"]) + 1,
                f"源报告字段标注本次材料类型或期间为“{declared_form}”；章节适用性应按该字段判断，不能仅按市场推定。",
                basis="source_report",
                scope="source_observation",
                evidence={"field": "form_type/report_type/fiscal_period"},
            )
        )

    declared_standard = _compact(report.get("accounting_standard"), limit=48)
    if declared_standard:
        sections["accounting_quality"].append(
            _insight(
                market_code,
                "accounting_quality",
                len(sections["accounting_quality"]) + 1,
                f"源报告字段标注会计准则为“{declared_standard}”；本报告以该字段为当前材料口径，不用市场常见准则覆盖。",
                basis="source_report",
                scope="source_observation",
                evidence={"field": "accounting_standard"},
            )
        )

    kind = _entity_kind(profile)
    if kind:
        kind_label = {"bank": "银行", "insurance": "保险机构", "financial": "金融机构"}.get(kind, "一般企业")
        sections["business_overview"].append(
            _insight(
                market_code,
                "business_overview",
                len(sections["business_overview"]) + 1,
                f"实体画像字段将分析对象标记为{kind_label}；该字段仅用于选择指标模板，仍须用当前报告核验具体业务和监管口径。",
                basis="entity_profile",
                scope="source_observation",
                evidence={"field": "kind/financial_institution"},
            )
        )

    for topic in source_topics:
        chapter_id = _ROLE_TO_CHAPTER.get(str(topic.get("role") or ""))
        if chapter_id is None:
            continue
        # Keep the normal report compact: the complete recognized topic list
        # remains available at source_topics, while a chapter receives at most
        # two topic observations.
        existing_topic_count = sum(item.get("basis") == "section_catalog" for item in sections[chapter_id])
        if existing_topic_count >= 2:
            continue
        sections[chapter_id].append(
            _insight(
                market_code,
                chapter_id,
                len(sections[chapter_id]) + 1,
                str(topic["text"]),
                basis="section_catalog",
                scope="topic_observation",
                evidence=dict(topic["evidence"]),
            )
        )

    check_summary = _summarize_financial_checks(checks)
    sections["accounting_quality"].append(
        _insight(
            market_code,
            "accounting_quality",
            len(sections["accounting_quality"]) + 1,
            check_summary["chapter_text"],
            basis="financial_checks" if checks else "input_boundary",
            scope="quality_observation",
            evidence={"field": "financial_checks"} if checks else {},
        )
    )

    warnings = _quality_warnings(
        market_code=market_code,
        config=config,
        unsupported=unsupported,
        report=report,
        checks=checks,
        check_summary=check_summary,
        source_topics=source_topics,
        topic_meta=topic_meta,
        source_metadata=metadata,
    )
    context = {
        "policy_context": str(config["reporting_context"]),
        "declared_form_type": declared_form or None,
        "declared_accounting_standard": declared_standard or None,
        "source_family": _compact(report.get("source_family"), limit=48) or None,
        "period_end": _compact(report.get("period_end"), limit=24) or None,
        "boundary_note": "市场政策用于选择分析口径，不构成公司事实；公司事实仍须由当前 ResearchIdentity 下的报告、指标或证据支持。",
    }
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "market": {"code": market_code, "label": str(config["label"])},
        "reporting_context": context,
        "sections": sections,
        "source_topics": source_topics,
        "quality": {
            "status": "degraded" if warnings else "ready",
            "warnings": warnings,
            "warning_summary": [str(item["message"]) for item in warnings],
        },
    }


def build_market_policy(
    market: str,
    source_report: Mapping[str, Any] | None = None,
    source_metadata: Mapping[str, Any] | None = None,
    financial_checks: Mapping[str, Any] | None = None,
    entity_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Short alias intended for renderer imports."""

    return build_analysis_market_policy(
        market,
        source_report=source_report,
        source_metadata=source_metadata,
        financial_checks=financial_checks,
        entity_profile=entity_profile,
    )


def normalize_market_code(market: Any) -> str:
    value = re.sub(r"[\s/]+", "_", str(market or "").strip().upper())
    return _MARKET_ALIASES.get(value, value or "UNKNOWN")


def _fallback_policy(market_code: str) -> dict[str, Any]:
    prefix = "当前市场未配置专用政策，"
    return {
        "label": f"未识别市场（{market_code}）",
        "reporting_context": "仅按源报告字段执行的保守分析语境",
        "usual_standards": (),
        "chapters": {
            "business_overview": (
                prefix + "业务分析仅使用当前报告的直接证据，不补写市场惯例。",
                "业务、客户、分部和地域结论均须绑定当前报告定位，缺失时明确标为不可用。",
            ),
            "risk_factors": (
                prefix + "风险分析只保留当前报告明确披露且可定位的主题。",
                "没有风险章节或证据时不判断风险不存在，也不以市场常识代替公司披露。",
            ),
            "controls": (
                prefix + "控制与治理结论只来自当前报告中的直接材料。",
                "制度描述、有效性判断、审计结论和整改状态必须分别提供证据。",
            ),
            "accounting_quality": (
                prefix + "会计准则、币种、单位和合并范围完全以源报告字段为准。",
                "只有期间、币种、范围和会计基础一致的指标才允许进行比较。",
            ),
            "tracking": (
                prefix + "后续跟踪只连接同一研究身份下的可比报告。",
                "没有后续数据时明确标为未执行，不生成稳定、改善或恶化结论。",
            ),
        },
    }


def _insight(
    market_code: str,
    chapter_id: str,
    index: int,
    text: str,
    *,
    basis: str,
    scope: str,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "insight_id": f"{market_code.lower()}-{chapter_id}-{index:02d}",
        "text": text,
        "basis": basis,
        "scope": scope,
        "evidence": dict(evidence or {}),
    }


def _recognize_section_topics(section_catalog: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not isinstance(section_catalog, Sequence) or isinstance(section_catalog, (str, bytes)):
        return [], {"catalog_count": 0, "recognized_count": 0, "truncated_count": 0}
    entries: list[dict[str, Any]] = []
    for raw in section_catalog:
        if not isinstance(raw, Mapping):
            continue
        excerpt = _compact(raw.get("excerpt"), limit=6000).lower()
        if not excerpt:
            continue
        themes = [label for label, terms in _TOPIC_RULES if any(term in excerpt for term in terms)]
        if not themes:
            continue
        role = _compact(raw.get("role") or raw.get("section_role"), limit=40).lower() or "other"
        locator = _safe_relative_locator(raw.get("file") or raw.get("local_source_id"))
        heading = _compact(raw.get("heading"), limit=120)
        evidence_ids = _evidence_ids(raw)
        evidence: dict[str, Any] = {"kind": "section_catalog", "role": role}
        if locator:
            evidence["locator"] = locator
        if heading:
            evidence["heading"] = heading
        if evidence_ids:
            evidence["evidence_ids"] = evidence_ids
        entries.append(
            {
                "role": role,
                "text": "原文涉及主题：" + "、".join(themes[:8]) + "。该标记仅用于章节导航，不构成公司事实结论。",
                "themes": themes[:8],
                "evidence": evidence,
            }
        )
    entries.sort(
        key=lambda item: (
            str(item.get("role") or ""),
            str((item.get("evidence") or {}).get("locator") or ""),
            str((item.get("evidence") or {}).get("heading") or ""),
            tuple(item.get("themes") or ()),
        )
    )
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for item in entries:
        key = (
            item["role"],
            (item["evidence"] or {}).get("locator"),
            tuple(item["themes"]),
        )
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(item)
    limit = 24
    return deduplicated[:limit], {
        "catalog_count": sum(isinstance(item, Mapping) for item in section_catalog),
        "recognized_count": len(deduplicated),
        "truncated_count": max(len(deduplicated) - limit, 0),
    }


def _financial_checks_payload(
    financial_checks: Mapping[str, Any] | None,
    source_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    payload: Any = financial_checks
    if not isinstance(payload, Mapping):
        payload = source_metadata.get("financial_checks")
    if not isinstance(payload, Mapping):
        return {}
    nested = payload.get("financial_checks")
    return dict(nested) if isinstance(nested, Mapping) else dict(payload)


def _summarize_financial_checks(checks: Mapping[str, Any]) -> dict[str, Any]:
    if not checks:
        return {
            "status": "missing",
            "warning_count": 0,
            "failure_count": 0,
            "chapter_text": "当前输入未提供财务检查结果；本章只能说明数据边界，不能将指标质量判断为通过。",
        }
    status = _compact(checks.get("overall_status") or checks.get("status"), limit=24).lower() or "unknown"
    summary = checks.get("summary") if isinstance(checks.get("summary"), Mapping) else {}
    warning_count = _count_messages(checks, ("warnings", "issues"))
    failure_count = _count_messages(checks, ("failures", "errors"))
    warning_count = max(warning_count, _safe_int(summary.get("warning")))
    failure_count = max(failure_count, _safe_int(summary.get("fail")), _safe_int(summary.get("failed")))
    status_label = {
        "pass": "通过",
        "passed": "通过",
        "ok": "通过",
        "ready": "通过",
        "warning": "存在告警",
        "warn": "存在告警",
        "fail": "失败",
        "failed": "失败",
        "error": "失败",
        "unknown": "未标注",
    }.get(status, f"源字段为 {status}")
    detail = f"，汇总到 {warning_count} 条告警、{failure_count} 条失败项" if warning_count or failure_count else ""
    return {
        "status": status,
        "warning_count": warning_count,
        "failure_count": failure_count,
        "chapter_text": f"财务检查状态为{status_label}{detail}；检查结果用于限定结论强度，不能替代原始报表和证据定位。",
    }


def _quality_warnings(
    *,
    market_code: str,
    config: Mapping[str, Any],
    unsupported: bool,
    report: Mapping[str, Any],
    checks: Mapping[str, Any],
    check_summary: Mapping[str, Any],
    source_topics: Sequence[Mapping[str, Any]],
    topic_meta: Mapping[str, int],
    source_metadata: Mapping[str, Any],
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if unsupported:
        warnings.append(_warning("unsupported_market", "市场代码未命中六市场政策，已退回仅依赖源报告字段的保守政策。"))

    standard = _compact(report.get("accounting_standard"), limit=48)
    if not standard:
        warnings.append(
            _warning("accounting_standard_missing", "源报告未标注会计准则，报告不得按市场惯例补写实际采用准则。")
        )
    elif not _matches_usual_standard(standard, config.get("usual_standards") or ()):
        warnings.append(
            _warning(
                "accounting_standard_requires_review",
                f"源报告会计准则字段为“{standard}”，与该市场常用政策标签不同；分析仍应以源字段为准并人工复核。",
            )
        )

    if not checks:
        warnings.append(_warning("financial_checks_missing", "未提供财务检查结果，不能将数据质量状态提升为通过。"))
    elif (
        str(check_summary.get("status") or "") not in {"pass", "passed", "ok", "ready"}
        or int(check_summary.get("warning_count") or 0)
        or int(check_summary.get("failure_count") or 0)
    ):
        warnings.append(_warning("financial_checks_not_pass", str(check_summary["chapter_text"])))

    catalog = source_metadata.get("section_catalog")
    catalog_present = isinstance(catalog, list) and bool(catalog)
    if market_code == "US":
        roles = {
            _compact(item.get("role") or item.get("section_role"), limit=40).lower()
            for item in catalog or ()
            if isinstance(item, Mapping)
        }
        missing_roles = [role for role in _EXPECTED_US_ROLES if role not in roles]
        if not catalog_present:
            warnings.append(
                _warning(
                    "sec_section_catalog_missing",
                    "SEC 材料未提供章节目录，Business、Risk Factors、MD&A、附注和 Controls 的分析均须降级。",
                )
            )
        elif missing_roles:
            warnings.append(
                _warning(
                    "sec_section_roles_incomplete",
                    "SEC 章节目录未识别到以下角色：" + "、".join(missing_roles) + "；缺失角色不等同于发行人未披露。",
                )
            )
    if catalog_present and not source_topics:
        warnings.append(
            _warning("section_topics_unrecognized", "章节目录存在，但英文摘录未命中受控主题词；未据此生成公司事实。")
        )
    if int(topic_meta.get("truncated_count") or 0) > 0:
        warnings.append(_warning("section_topics_truncated", "可识别章节主题较多，政策输出仅保留前 24 个确定性定位。"))

    source_quality = _compact(report.get("quality_status"), limit=24).lower()
    if source_quality and source_quality not in {"pass", "passed", "ready", "completed"}:
        warnings.append(
            _warning("source_quality_not_pass", "源报告质量字段未标记为通过，分析结论应保持降级并保留原状态。")
        )
    return warnings


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": "warning", "message": message}


def _matches_usual_standard(value: str, usual_standards: Sequence[str]) -> bool:
    normalized = _standard_token(value)
    return any(normalized == _standard_token(item) for item in usual_standards)


def _standard_token(value: Any) -> str:
    return re.sub(r"[^A-Z0-9\u4e00-\u9fff]+", "", str(value or "").upper())


def _entity_kind(profile: Mapping[str, Any]) -> str:
    explicit = _compact(profile.get("kind"), limit=32).lower()
    if explicit in {"bank", "insurance", "financial", "general"}:
        return explicit
    if profile.get("financial_institution") is True:
        return "financial"
    return ""


def _evidence_ids(raw: Mapping[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("evidence_ids", "evidence_id"):
        value = raw.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value:
            values.append(value)
    return list(dict.fromkeys(_compact(item, limit=160) for item in values if _compact(item, limit=160)))[:12]


def _safe_relative_locator(value: Any) -> str:
    text = _compact(value, limit=300).replace("\\", "/")
    if not text:
        return ""
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        return ""
    return path.as_posix()


def _compact(value: Any, *, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _count_messages(payload: Mapping[str, Any], keys: Sequence[str]) -> int:
    count = 0
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            count += len(value)
        elif isinstance(value, Mapping):
            count += len(value)
        elif value:
            count += 1
    return count


def _safe_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "CHAPTER_IDS",
    "POLICY_SCHEMA_VERSION",
    "SUPPORTED_MARKETS",
    "build_analysis_market_policy",
    "build_market_policy",
    "normalize_market_code",
]
