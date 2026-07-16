#!/usr/bin/env python3
"""Generate deterministic SIQ v1.1 section drafts from checkpoints.

This script is intentionally conservative: it uses only checkpointed metrics,
evidence, and outline statements. Missing peer, market, governance, or model
inputs are surfaced as review items instead of being invented.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from provenance_utils import (
    evidence_id_from_record,
    load_provenance_lookup,
    normalize_evidence_package,
)


SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_JSON = SCRIPT_DIR.parent / "templates" / "siq_analysis_report_v1.1.json"
SECTION_SCHEMA_JSON = SCRIPT_DIR.parent / "templates" / "section_drafts.schema.json"


ALIASES = {
    "net_profit_parent": ["parent_net_profit", "归属于上市公司股东的净利润"],
    "net_operating_cash_flow": ["operating_cash_flow_net", "经营活动产生的现金流量净额"],
    "deducted_parent_net_profit": ["扣除非经常性损益后的归属于上市公司股东的净利润"],
    "monetary_funds": ["monetary_capital", "货币资金"],
    "gross_margin": ["gross_profit_margin", "毛利率"],
    "debt_to_asset_ratio": ["asset_liability_ratio", "资产负债率"],
    "capital_expenditure": ["cash_for_purchases_investments", "购建固定资产、无形资产和其他长期资产支付的现金"],
    "accounts_receivable": ["应收账款"],
    "notes_receivable": ["应收票据"],
    "inventory": ["存货"],
    "operating_cost": ["营业成本"],
    "operating_profit": ["营业利润"],
    "total_profit": ["利润总额"],
    "total_assets": ["资产总计"],
    "total_liabilities": ["负债合计"],
    "equity_attributable_parent": ["归属于母公司所有者权益合计"],
    "current_assets": ["流动资产合计"],
    "current_liabilities": ["流动负债合计"],
    "short_term_borrowings": ["短期借款"],
    "current_portion_noncurrent_liabilities": ["一年内到期的非流动负债"],
    "contract_liabilities": ["合同负债"],
    "interest_expense": ["利息费用"],
}

MONETARY_METRIC_KEYS = {
    "operating_revenue",
    "total_operating_revenue",
    "operating_cost",
    "parent_net_profit",
    "net_profit_parent",
    "deducted_parent_net_profit",
    "operating_cash_flow_net",
    "net_operating_cash_flow",
    "total_assets",
    "total_liabilities",
    "equity_attributable_parent",
    "monetary_capital",
    "monetary_funds",
    "inventory",
    "accounts_receivable",
    "notes_receivable",
    "short_term_borrowings",
    "current_portion_noncurrent_liabilities",
    "current_assets",
    "current_liabilities",
    "contract_liabilities",
    "cash_for_purchases",
    "cash_for_purchases_investments",
    "capital_expenditure",
    "operating_profit",
    "total_profit",
    "interest_expense",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return load_json(path)


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_section_defs() -> list[tuple[str, str]]:
    template = load_json(TEMPLATE_JSON)
    sections = sorted(template.get("sections", []), key=lambda item: item.get("order", 0))
    return [(str(item["section_id"]), str(item["title"])) for item in sections]


SECTION_DEFS = load_section_defs()
SECTION_IDS = [sid for sid, _ in SECTION_DEFS]
SECTION_TITLES = dict(SECTION_DEFS)


def load_section_meta() -> dict[str, dict[str, Any]]:
    template = load_json(TEMPLATE_JSON)
    result: dict[str, dict[str, Any]] = {}
    for section in template.get("sections", []) or []:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("section_id") or "")
        if not section_id:
            continue
        result[section_id] = {
            "section_type": str(section.get("section_type") or "cfo_analysis"),
            "preferred_blocks": [
                str(item)
                for item in section.get("preferred_blocks", []) or []
                if str(item).strip()
            ],
        }
    return result


SECTION_META = load_section_meta()

NOISY_QUALITATIVE_TERMS = [
    "现金分红",
    "利润分配",
    "退市风险警示",
    "合并财务报表的编制方法",
    "控制的判断标准",
    "主要财务指标",
    "报告期末公司前三年主要会计数据",
    "基本每股收益",
    "导致退市风险警示的原因",
    "公司股票被实施退市风险警示",
    "同类业务采用不同经营模式",
    "前五名销售客户 □适用",
    "前五名供应商 □适用",
    "产品质量保证金",
    "分部信息",
    "单位：元 币种",
    "公司报告期内业务、产品或服务发生重大变化或调整有关情况",
]

LOW_VALUE_REPORT_PHRASES = [
    "执行摘要必须",
    "本节重点是",
    "正确写法是",
    "当前生成器不联网",
    "报告使用 metric_snapshot",
    "避免只罗列",
    "不构成投资建议",
    "不得输出",
    "不得把",
    "只能写",
]

SOURCE_LABEL_PREFIX_RE = re.compile(r"^【[^】]+】")

SECTION_SYNTHESIS_FOCUS = {
    "executive_summary": ("经营安全与盈利质量", "收入、扣非利润、现金流和负债覆盖是否同向", "核心矛盾是否缓释"),
    "key_changes": ("年度变化的质量", "增长、利润、现金流和资产负债表是否同向", "变化是否具有持续性"),
    "operating_quality": ("经营质量", "收入增长是否转化为回款、周转和合同负债支撑", "经营拐点是否被财务变量验证"),
    "profitability_and_cost": ("盈利能力", "毛利率、费用率、扣非利润和非经常项目的贡献", "利润修复是否依赖一次性因素"),
    "asset_quality_working_capital": ("资产质量与营运资本", "存货、应收、合同负债和周转效率", "收入质量是否被资产端拖累"),
    "debt_liquidity": ("偿债安全", "短债、现金、有息负债和经营现金流覆盖", "流动性压力是否扩大"),
    "cash_flow_quality": ("现金流质量", "经营现金流、资本开支和自由现金流的匹配", "利润含金量是否改善"),
    "industry_competition": ("行业竞争位置", "同业分位、产品结构、价格竞争和现金转化", "竞争优势是否进入报表"),
    "strategy_policy_external_risk": ("战略兑现质量", "研发、资本开支、产品结构和现金流是否验证管理层战略", "战略叙事是否被财务变量支撑"),
    "governance_compliance_shareholders": ("治理与合规风险", "审计、诉讼、股东承诺和资本动作", "治理变量是否影响财务可信度"),
    "valuation_expectation_gap": ("估值预期差", "基本面锚、市场数据缺口和同业估值可比性", "估值讨论是否具备足够证据"),
    "risk_chain_scenario": ("风险链条", "关键变量恶化如何传导到利润、现金流和资产质量", "哪些反证会推翻当前结论"),
    "tracking_checklist": ("后续跟踪", "改善信号、恶化信号和数据源频率", "跟踪体系是否可执行"),
    "data_quality_traceability": ("数据质量与溯源", "证据覆盖、缺失字段和可复核链接", "报告结论的可信边界"),
}


def values_for(snapshot: dict[str, Any], key: str) -> dict[str, Any]:
    item = metric(snapshot, key)
    values = item.get("values") if isinstance(item.get("values"), dict) else {}
    return values


def normalize_value(item: dict[str, Any], value: Any) -> Any:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return value
    unit = str(item.get("unit") or "").strip()
    key = str(item.get("canonical_name") or item.get("metric_key") or "")
    if unit in {"元", "人民币元", "CNY"}:
        return value / 100_000_000
    if unit == "万元":
        return value / 10_000
    if not unit and key in MONETARY_METRIC_KEYS and abs(value) >= 100_000:
        return value / 100_000_000
    return value


def metric(snapshot: dict[str, Any], key: str) -> dict[str, Any]:
    metrics = snapshot.get("metrics")
    if not isinstance(metrics, dict):
        metrics = snapshot.get("key_metrics") if isinstance(snapshot.get("key_metrics"), dict) else {}
    if key in metrics and isinstance(metrics[key], dict):
        return metrics[key]
    for alias in ALIASES.get(key, []):
        if alias in metrics and isinstance(metrics[alias], dict):
            return metrics[alias]
    return {}


def metric_value(snapshot: dict[str, Any], key: str, year: str = "2025") -> Any:
    item = metric(snapshot, key)
    values = item.get("values") if isinstance(item.get("values"), dict) else {}
    return normalize_value(item, values.get(year))


def yoy(snapshot: dict[str, Any], key: str, year: int) -> Any:
    item = metric(snapshot, key)
    existing = item.get("yoy_change")
    if isinstance(existing, (int, float)) and math.isfinite(existing):
        return existing
    values = item.get("values") if isinstance(item.get("values"), dict) else {}
    current = normalize_value(item, values.get(str(year)))
    previous = normalize_value(item, values.get(str(year - 1)))
    if isinstance(current, (int, float)) and isinstance(previous, (int, float)) and previous:
        return (current - previous) / abs(previous) * 100
    return None


def fmt(value: Any, suffix: str = "", missing: str = "未返回") -> str:
    if value is None:
        return missing
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return missing
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def pct(value: Any) -> str:
    return fmt(value, "%")


def yi(value: Any) -> str:
    return fmt(value, "亿元")


def compact_qualitative_text(value: Any, limit: int = 260) -> str:
    text = re.sub(r"（证据：[^）]+）", "", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"。+；", "；", text)
    text = re.sub(r"。+；", "；", text)
    text = re.sub(r"；+", "；", text)
    if len(text) <= limit:
        return text
    cut = max(text.rfind("。", 0, limit), text.rfind("；", 0, limit), text.rfind("，", 0, limit))
    if cut >= max(30, limit // 3):
        return text[: cut + 1].rstrip()
    return text[:limit].rstrip("，。；; ")


def is_noisy_qualitative_text(value: Any) -> bool:
    text = str(value or "")
    return (
        not text.strip()
        or any(term in text for term in NOISY_QUALITATIVE_TERMS)
        or any(term in text for term in LOW_VALUE_REPORT_PHRASES)
    )


def clean_qualitative_texts(items: list[Any], limit: int, text_limit: int = 260) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if is_noisy_qualitative_text(item):
            continue
        text = compact_qualitative_text(item, text_limit)
        key = re.sub(r"\s+", "", text)
        if not key or key in seen:
            continue
        result.append(text)
        seen.add(key)
        if len(result) >= limit:
            break
    return result


def clean_section_items(section_id: str, items: list[str]) -> list[str]:
    text_limit = 280 if section_id in {"strategy_policy_external_risk", "governance_compliance_shareholders"} else 380
    return clean_qualitative_texts(items, len(items), text_limit)


def clean_visible_items(items: list[str], text_limit: int = 360) -> list[str]:
    return clean_qualitative_texts(items, len(items), text_limit)


def ratio(numerator: Any, denominator: Any, multiplier: float = 1.0) -> Any:
    if isinstance(numerator, (int, float)) and isinstance(denominator, (int, float)) and denominator:
        return numerator / denominator * multiplier
    return None


def first_evidence(evidence_package: dict[str, Any], metric_key: str) -> dict[str, Any]:
    financial = evidence_package.get("financial_evidence")
    if not isinstance(financial, dict):
        return {}
    candidates = [metric_key] + ALIASES.get(metric_key, [])
    for key in candidates:
        item = financial.get(key)
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence")
        if isinstance(evidence, list) and evidence and isinstance(evidence[0], dict):
            return evidence[0]
    return {}


def evidence_id(evidence_package: dict[str, Any], metric_key: str) -> str:
    ev = first_evidence(evidence_package, metric_key)
    return evidence_id_from_record(metric_key, ev)


def evidence_ids(evidence_package: dict[str, Any], keys: list[str]) -> list[str]:
    result: list[str] = []
    for key in keys:
        item = evidence_id(evidence_package, key)
        if item not in result:
            result.append(item)
    return result


def company_dir_from_inventory(wiki_inventory: dict[str, Any]) -> Path | None:
    raw = wiki_inventory.get("company_dir") if isinstance(wiki_inventory, dict) else None
    if not raw:
        return None
    path = Path(str(raw))
    return path if path.exists() else None


def missing_fields(snapshot: dict[str, Any], keys: list[str], year: int) -> list[str]:
    missing: list[str] = []
    for key in keys:
        if metric_value(snapshot, key, str(year)) is None:
            missing.append(key)
    return missing


def q_items(qualitative: dict[str, Any], bucket: str, limit: int = 3) -> list[str]:
    candidates: list[Any] = []
    interpretation = qualitative.get("interpretation")
    if isinstance(interpretation, dict):
        items = interpretation.get(bucket)
        if isinstance(items, list) and items:
            candidates.extend(items[: limit * 4])
    buckets = qualitative.get("buckets")
    if isinstance(buckets, dict):
        entries = buckets.get(bucket)
        if isinstance(entries, list):
            candidates.extend(
                item.get("text")
                for item in entries[: limit * 4]
                if isinstance(item, dict) and str(item.get("text") or "").strip()
            )
    return clean_qualitative_texts(candidates, limit)


def q_evidence(qualitative: dict[str, Any], buckets: list[str], limit: int = 8) -> list[str]:
    result: list[str] = []
    bucket_map = qualitative.get("buckets")
    if not isinstance(bucket_map, dict):
        return result
    for bucket in buckets:
        entries = bucket_map.get(bucket)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for evidence in entry.get("evidence_ids") or []:
                evidence_id = f"qualitative:{evidence}"
                if evidence_id not in result:
                    result.append(evidence_id)
                if len(result) >= limit:
                    return result
    return result


def q_missing(qualitative: dict[str, Any], buckets: list[str]) -> list[str]:
    bucket_map = qualitative.get("buckets")
    if not isinstance(bucket_map, dict):
        return [f"qualitative_{bucket}" for bucket in buckets]
    return [f"qualitative_{bucket}" for bucket in buckets if not bucket_map.get(bucket)]


def qualitative_ok(qualitative: dict[str, Any], buckets: list[str]) -> bool:
    return not q_missing(qualitative, buckets)


def market_value(market: dict[str, Any], section: str, key: str) -> Any:
    item = market.get(section)
    if isinstance(item, dict):
        return item.get(key)
    return None


def market_items(market: dict[str, Any], limit: int = 4) -> list[str]:
    items = market.get("interpretation")
    if isinstance(items, list) and items:
        return [str(item) for item in items[:limit] if str(item).strip()]
    return []


def market_evidence(market: dict[str, Any]) -> list[str]:
    evidence = ["market_snapshot:market_snapshot.json"]
    source_files = market_value(market, "market", "source_files")
    if isinstance(source_files, list):
        evidence.extend(f"market_source:{source}" for source in source_files[:3])
    return evidence


def market_missing(market: dict[str, Any]) -> list[str]:
    missing: list[str] = [] if market.get("strict_ok") else ["market_price_or_market_cap"]
    valuation = market.get("valuation") if isinstance(market.get("valuation"), dict) else {}
    if valuation.get("valuation_percentile") is None:
        missing.append("valuation_percentile")
    if valuation.get("consensus_expectation") is None:
        missing.append("consensus_expectation")
    return missing


def research_items(industry_research: dict[str, Any], limit: int = 5) -> list[str]:
    items = industry_research.get("interpretation")
    if isinstance(items, list) and items:
        return [str(item) for item in items[:limit] if str(item).strip()]
    results = industry_research.get("results")
    if isinstance(results, list):
        output: list[str] = []
        for item in results[:limit]:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("url") or "未命名来源"
            snippet = item.get("snippet") or "未返回摘要"
            summary = " ".join(str(snippet).split())[:180]
            output.append(f"外部行业来源《{title}》提示：{summary}。")
        return output
    return []


def research_evidence(industry_research: dict[str, Any]) -> list[str]:
    evidence = ["industry_research:industry_research.json"]
    results = industry_research.get("results")
    if isinstance(results, list):
        for idx, item in enumerate(results[:3]):
            if not isinstance(item, dict):
                continue
            provider = item.get("provider") or "external"
            evidence.append(f"industry_research:{provider}:{idx}")
    return evidence


def research_missing(industry_research: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not industry_research.get("strict_ok"):
        missing.append("tavily_exa_industry_research")
    provider_status = industry_research.get("provider_status")
    if isinstance(provider_status, dict):
        for provider in ["tavily", "exa"]:
            status = provider_status.get(provider) if isinstance(provider_status.get(provider), dict) else {}
            if not status.get("ok"):
                missing.append(f"{provider}_industry_research")
    elif not industry_research:
        missing.extend(["tavily_industry_research", "exa_industry_research"])
    return sorted(set(missing))


def classify_profit(value: Any) -> str:
    if isinstance(value, (int, float)):
        return "亏损" if value < 0 else "盈利"
    return "盈利状态未确认"


def classify_ocf(value: Any) -> str:
    if isinstance(value, (int, float)):
        return "经营现金流为负" if value < 0 else "经营现金流为正"
    return "经营现金流未确认"


def classify_debt(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "杠杆水平未确认"
    if value >= 80:
        return "高杠杆"
    if value >= 65:
        return "杠杆偏高"
    return "杠杆处于可观察区间"


def outline_list(outline: dict[str, Any], key: str, fallback: list[str]) -> list[str]:
    value = outline.get(key)
    if isinstance(value, list) and value:
        return [str(item) for item in value if str(item).strip()]
    return fallback


def source_industry_path(preflight: dict[str, Any], snapshot: dict[str, Any], peer_metrics: dict[str, Any]) -> str:
    peer_path = peer_metrics.get("target_industry_path")
    if str(peer_path or "").strip():
        return str(peer_path).strip()
    for source in [preflight, snapshot]:
        parts = [str(source.get(key) or "").strip() for key in ["industry_sw1", "industry_sw2", "industry_sw3"]]
        parts = [item for item in parts if item]
        if parts:
            return " > ".join(parts)
        industry = str(source.get("industry") or "").strip()
        if industry:
            return industry
    return "所属行业"


def industry_label(industry_path: str) -> str:
    parts = [part.strip() for part in str(industry_path or "").split(">") if part.strip()]
    return parts[-1] if parts else "所属行业"


def business_dimensions(
    strategy_items: list[str],
    product_items: list[str],
    operation_items: list[str],
    industry_items: list[str],
    external_risk_items: list[str],
) -> str:
    dimensions: list[str] = []
    concepts: set[str] = set()

    def add(label: str, concept: str) -> None:
        if concept in concepts:
            return
        dimensions.append(label)
        concepts.add(concept)

    if product_items:
        add("产品/服务结构", "product")
    if operation_items:
        add("渠道/区域与运营效率", "channel")
    if industry_items:
        add("价格与竞争格局", "price")
    if strategy_items:
        add("业务线贡献和战略项目", "business_line")
    if external_risk_items:
        add("政策、供应链和区域变量", "external")
    for fallback, concept in [
        ("产品/服务结构", "product"),
        ("渠道/区域", "channel"),
        ("价格体系", "price"),
        ("业务线贡献", "business_line"),
        ("费用投入", "expense"),
    ]:
        add(fallback, concept)
    return "、".join(dimensions[:5])


def compact_text(section: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in section.get("narrative_blocks") or []:
        if isinstance(block, dict):
            parts.append(str(block.get("title") or ""))
            items = block.get("items")
            if isinstance(items, list):
                parts.extend(str(item) for item in items)
    for key in ["facts", "calculations", "judgements", "risks_or_improvement_conditions"]:
        value = section.get(key)
        if isinstance(value, list):
            parts.extend(str(item) for item in value)
    return "".join(parts).replace(" ", "").replace("\n", "")


def narrative_block(title: str, items: list[str], role: str = "analysis") -> dict[str, Any]:
    return {
        "title": title,
        "role": role,
        "items": [item for item in items if item],
    }


def strip_source_label(text: str) -> str:
    return SOURCE_LABEL_PREFIX_RE.sub("", str(text or "")).strip()


def select_anchor(items: list[str], keywords: list[str], fallback_index: int = 0) -> str:
    clean = [strip_source_label(item) for item in items if strip_source_label(item)]
    for keyword in keywords:
        for item in clean:
            if keyword in item:
                return item
    if clean:
        return clean[min(fallback_index, len(clean) - 1)]
    return ""


def build_section_synthesis(
    section_id: str,
    facts: list[str],
    calculations: list[str],
    judgements: list[str],
    risks: list[str],
) -> dict[str, Any] | None:
    focus = SECTION_SYNTHESIS_FOCUS.get(section_id)
    if not focus:
        return None
    subject, verification_axis, boundary_axis = focus
    evidence_anchor = select_anchor(facts, ["营业收入", "归母净利润", "经营现金流", "毛利率", "资产负债率", "研发", "同业", "公司"], 0).rstrip("。；; ")
    model_anchor = select_anchor(calculations, ["同比", "比率", "分位", "自由现金流", "杜邦", "覆盖", "研发"], 0).rstrip("。；; ")
    judgement_anchor = select_anchor(judgements, ["核心", "需要", "验证", "改善", "压力", "质量"], 0).rstrip("。；; ")
    risk_anchor = select_anchor(risks, ["风险", "恶化", "改善", "推翻", "验证", "缺口"], 0).rstrip("。；; ")

    items: list[str] = []
    if evidence_anchor:
        sentence = (
            f"本节围绕{subject}展开。已确认的本地证据显示，{evidence_anchor} "
            f"因此不能只看单一指标，需要沿着{verification_axis}进行交叉验证。"
        )
        items.append(sentence)
    if model_anchor or judgement_anchor:
        sentence_parts = [f"从模型和经营解释看，{model_anchor}" if model_anchor else ""]
        if judgement_anchor:
            sentence_parts.append(f"对应的分析判断是：{judgement_anchor}")
        sentence = "；".join(part for part in sentence_parts if part).rstrip("。") + "。"
        items.append(sentence)
    if risk_anchor:
        items.append(
            f"结论边界在于{boundary_axis}：{risk_anchor} 报告会把这类信息作为后续跟踪或复核条件，而不是直接升级为确定性结论。"
        )

    clean_items = clean_visible_items(items, 520)
    if len(clean_items) < 2:
        return None
    return narrative_block("本节综合解读", clean_items[:3], "synthesis")


def build_narrative_blocks(
    section_id: str,
    facts: list[str],
    calculations: list[str],
    judgements: list[str],
    risks: list[str],
) -> list[dict[str, Any]]:
    preferred = SECTION_META.get(section_id, {}).get("preferred_blocks") or []
    block_items: dict[str, list[str]] = {}
    block_roles: dict[str, str] = {}

    def put(title: str, items: list[str], role: str = "analysis") -> None:
        clean = [item for item in items if item]
        if not clean:
            return
        block_items.setdefault(title, []).extend(clean)
        block_roles.setdefault(title, role)

    synthesis = build_section_synthesis(section_id, facts, calculations, judgements, risks)

    if section_id == "executive_summary":
        put("经营状态定性", facts[:2], "diagnosis")
        put("财务健康度速览", [*calculations[:2], *facts[2:4]], "table")
        put("核心结论", judgements[:4], "diagnosis")
        put("改变结论的条件", risks[:5], "tracking")
    elif section_id == "key_changes":
        put("年度异动雷达", facts[:4], "table")
        put("改善/恶化/观察项", risks[:3], "diagnosis")
        put("三表联动解释", [*calculations[:2], *judgements[:2]], "bridge")
        put("口径与证据", facts[4:6] or calculations[2:3], "evidence")
    elif section_id == "operating_quality":
        put("收入变化分析", facts[:2], "diagnosis")
        put("收入与现金流匹配度", [*calculations[:2], *judgements[:1]], "bridge")
        put("经营稳定性评估", judgements[1:3], "diagnosis")
        put("业务韧性与待验证信号", risks[:4], "tracking")
    elif section_id == "profitability_and_cost":
        put("杜邦分析", [item for item in calculations if "杜邦" in item or "净利率" in item or "ROE" in item] or calculations[:2], "model")
        put("利润变化桥", facts[:3], "bridge")
        put("毛利率与成本成因", [*calculations[:1], *judgements[:2]], "diagnosis")
        put("费用/减值/非经常性损益", [*facts[2:4], *risks[:3]], "analysis")
    elif section_id == "asset_quality_working_capital":
        put("资产结构与安全垫", facts[:2], "diagnosis")
        put("存货分析", [item for item in facts + calculations + risks if "存货" in item][:4], "analysis")
        put("应收款项分析", [item for item in facts + calculations + risks if "应收" in item][:4], "analysis")
        put("现金转换周期", [item for item in calculations + judgements + risks if any(term in item for term in ["CCC", "DSO", "DIO", "DPO", "周转"])][:4], "model")
    elif section_id == "debt_liquidity":
        put("短期偿债能力", [*facts[:2], *calculations[:2]], "model")
        put("长期偿债能力", [*facts[2:4], *judgements[:1]], "diagnosis")
        put("现金覆盖与融资弹性", [*judgements[1:2], *risks[:2]], "bridge")
        put("Altman Z-Score 适用性", [item for item in calculations + risks if "Altman" in item or "Z-Score" in item] or risks[-2:], "model")
    elif section_id == "cash_flow_quality":
        put("现金流量表概览", facts[:3], "table")
        put("经营现金流与利润匹配度", [*calculations[:1], *judgements[:1]], "bridge")
        put("自由现金流", [item for item in calculations + judgements + risks if "自由现金流" in item or "资本开支" in item][:4], "model")
        put("现金流恶化/改善原因", [*judgements[1:3], *risks[:3]], "diagnosis")
    elif section_id == "industry_competition":
        put("行业周期判断", [*facts[:2], *judgements[:1]], "diagnosis")
        put("同业对比", [*facts[1:4], *calculations[:2]], "table")
        put("竞争位置", judgements[:3], "diagnosis")
        put("价格竞争与产品结构传导", risks[:4], "risk_chain")
    elif section_id == "strategy_policy_external_risk":
        put("管理层战略", facts[:3], "diagnosis")
        put("政策/区域/供应链变量", [*facts[3:5], *risks[:1]], "analysis")
        put("战略兑现的财务验证", [*calculations[:3], *judgements[:2]], "bridge")
        put("待验证事项", risks[:4], "tracking")
    elif section_id == "governance_compliance_shareholders":
        put("治理观察", facts[:3], "evidence")
        put("股东结构与资本动作", facts[3:5] or calculations[:1], "analysis")
        put("合规/审计/监管事项", [*calculations[:2], *judgements[:2]], "audit")
        put("治理风险信号", risks[:4], "risk_chain")
    elif section_id == "valuation_expectation_gap":
        put("估值数据缺口", facts[:2], "evidence")
        put("基本面锚", [*facts[1:3], *calculations[:2]], "model")
        put("市场预期差", judgements[:3], "diagnosis")
        put("A 股特有风险", risks[:4], "risk_chain")
    elif section_id == "risk_chain_scenario":
        put("主要风险链条", [*calculations[:2], *judgements[:2]], "risk_chain")
        put("情景推演", risks[:3], "scenario")
        put("可能推翻当前结论的证据", risks[3:6] or facts[:3], "tracking")
        put("风险缓释条件", judgements[2:4] or calculations[2:3], "analysis")
    elif section_id == "tracking_checklist":
        put("核心跟踪指标", [*facts[:2], *calculations[:1]], "tracking")
        put("改善信号", [item for item in risks if "改善" in item][:4] or risks[:2], "tracking")
        put("恶化信号", [item for item in risks if "恶化" in item or "推翻" in item or "转负" in item][:4] or risks[2:4], "tracking")
        put("跟踪频率与数据源", [*judgements[:3], *risks[-1:]], "evidence")
    elif section_id == "data_quality_traceability":
        put("数据来源", facts[:3], "evidence")
        put("数据质量检查", calculations[:4], "audit")
        put("关键证据索引", judgements[:2], "evidence")
        put("限制与免责声明", risks[:4], "audit")
    else:
        put(preferred[0] if preferred else "核心观察", facts[:3], "diagnosis")
        put(preferred[1] if len(preferred) > 1 else "模型与口径", calculations[:3], "model")
        put(preferred[2] if len(preferred) > 2 else "分析判断", judgements[:3], "analysis")
        put(preferred[3] if len(preferred) > 3 else "风险与验证", risks[:3], "tracking")

    blocks: list[dict[str, Any]] = []
    if synthesis:
        blocks.append(synthesis)
    ordered_titles = [title for title in preferred if title in block_items]
    ordered_titles.extend(title for title in block_items if title not in ordered_titles)
    for title in ordered_titles:
        blocks.append(narrative_block(title, block_items[title], block_roles.get(title, "analysis")))
    return blocks


def make_section(
    section_id: str,
    facts: list[str],
    calculations: list[str],
    judgements: list[str],
    risks: list[str],
    evidence: list[str],
    review_required: bool,
    missing: list[str],
) -> dict[str, Any]:
    facts_clean = clean_section_items(section_id, [item for item in facts if item])
    calculations_clean = clean_visible_items([item for item in calculations if item], 420)
    judgements_clean = clean_visible_items([item for item in judgements if item], 420)
    risks_clean = clean_visible_items([item for item in risks if item], 420)
    return {
        "section_id": section_id,
        "title": SECTION_TITLES[section_id],
        "section_type": SECTION_META.get(section_id, {}).get("section_type", "cfo_analysis"),
        "narrative_blocks": build_narrative_blocks(section_id, facts_clean, calculations_clean, judgements_clean, risks_clean),
        "facts": facts_clean,
        "calculations": calculations_clean,
        "judgements": judgements_clean,
        "risks_or_improvement_conditions": risks_clean,
        "evidence_ids": evidence or [f"{section_id}:evidence_missing"],
        "review_required": bool(review_required),
        "missing_fields": sorted(set(missing)),
    }


def validate_section_drafts(data: dict[str, Any]) -> dict[str, Any]:
    schema = load_json(SECTION_SCHEMA_JSON)
    required = schema["required_section_fields"]
    array_fields = set(schema["array_fields"])
    minimum_items = schema["minimum_items"]
    expected = schema["expected_section_ids"]
    min_len = int(schema["minimum_compact_text_length"])
    sections = data.get("sections")
    failures: list[str] = []
    warnings: list[str] = []
    if not isinstance(sections, list):
        failures.append("sections_not_list")
        sections = []
    actual = [str(section.get("section_id", "")) for section in sections if isinstance(section, dict)]
    if actual != expected:
        failures.append("section_order_invalid")
    if len(actual) != 14:
        failures.append(f"section_count_invalid:{len(actual)}")
    for section in sections:
        if not isinstance(section, dict):
            failures.append("section_not_object")
            continue
        sid = str(section.get("section_id", "unknown"))
        for field in required:
            if field not in section:
                failures.append(f"{sid}:missing_field:{field}")
        for field in array_fields:
            value = section.get(field)
            if not isinstance(value, list):
                failures.append(f"{sid}:field_not_list:{field}")
                continue
            minimum = int(minimum_items.get(field, 0))
            if len(value) < minimum:
                failures.append(f"{sid}:too_few_items:{field}:{len(value)}")
        if not isinstance(section.get("review_required"), bool):
            failures.append(f"{sid}:review_required_not_bool")
        if len(compact_text(section)) < min_len:
            failures.append(f"{sid}:thin_content")
        evidence = section.get("evidence_ids")
        if isinstance(evidence, list) and all(str(item).endswith(":missing") for item in evidence):
            warnings.append(f"{sid}:only_missing_evidence")
    return {
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
        "metrics": {
            "section_count": len(actual),
            "section_order_valid": actual == expected,
        },
    }


def build_sections(
    preflight: dict[str, Any],
    snapshot: dict[str, Any],
    evidence_package: dict[str, Any],
    outline: dict[str, Any],
    peer_metrics: dict[str, Any],
    qualitative: dict[str, Any],
    industry_research: dict[str, Any],
    market: dict[str, Any],
    year: int,
) -> list[dict[str, Any]]:
    company = preflight.get("company_short_name") or preflight.get("company_id") or snapshot.get("company_short_name") or "公司"
    task_id = preflight.get("task_id") or snapshot.get("task_id") or "未返回"

    revenue = metric_value(snapshot, "operating_revenue", str(year))
    revenue_prev = metric_value(snapshot, "operating_revenue", str(year - 1))
    revenue_yoy = yoy(snapshot, "operating_revenue", year)
    cost = metric_value(snapshot, "operating_cost", str(year))
    gross_margin = metric_value(snapshot, "gross_margin", str(year))
    if gross_margin is None and isinstance(revenue, (int, float)) and revenue and isinstance(cost, (int, float)):
        gross_margin = (revenue - cost) / revenue * 100
    profit = metric_value(snapshot, "net_profit_parent", str(year))
    deducted_profit = metric_value(snapshot, "deducted_parent_net_profit", str(year))
    ocf = metric_value(snapshot, "net_operating_cash_flow", str(year))
    ocf_yoy = yoy(snapshot, "net_operating_cash_flow", year)
    assets = metric_value(snapshot, "total_assets", str(year))
    liabilities = metric_value(snapshot, "total_liabilities", str(year))
    parent_equity = metric_value(snapshot, "equity_attributable_parent", str(year))
    cash = metric_value(snapshot, "monetary_funds", str(year))
    inventory = metric_value(snapshot, "inventory", str(year))
    receivable = metric_value(snapshot, "accounts_receivable", str(year))
    notes = metric_value(snapshot, "notes_receivable", str(year))
    current_assets = metric_value(snapshot, "current_assets", str(year))
    current_liabilities = metric_value(snapshot, "current_liabilities", str(year))
    short_debt = metric_value(snapshot, "short_term_borrowings", str(year))
    current_portion = metric_value(snapshot, "current_portion_noncurrent_liabilities", str(year))
    capex = metric_value(snapshot, "capital_expenditure", str(year))
    interest_expense = metric_value(snapshot, "interest_expense", str(year))
    fcf = metric_value(snapshot, "free_cash_flow", str(year))
    debt_ratio = metric_value(snapshot, "debt_to_asset_ratio", str(year))
    if debt_ratio is None:
        debt_ratio = ratio(liabilities, assets, 100)
    current_ratio = ratio(current_assets, current_liabilities)
    receivable_total = None
    if isinstance(receivable, (int, float)) or isinstance(notes, (int, float)):
        receivable_total = (receivable or 0) + (notes or 0)
    short_debt_total = None
    if isinstance(short_debt, (int, float)) or isinstance(current_portion, (int, float)):
        short_debt_total = (short_debt or 0) + (current_portion or 0)
    cash_short_debt_cover = ratio(cash, short_debt_total)
    ocf_profit_cover = ratio(ocf, profit)
    fcf_calc = fcf
    if isinstance(ocf, (int, float)) and isinstance(capex, (int, float)):
        fcf_calc = ocf - abs(capex)
    nonrecurring_gap = None
    if isinstance(profit, (int, float)) and isinstance(deducted_profit, (int, float)):
        nonrecurring_gap = profit - deducted_profit

    core = outline.get("core_judgment") or (
        f"{company} {year} 年处于经营质量、盈利修复和现金流安全再验证阶段。"
    )
    contradiction = outline.get("core_contradiction") or "收入、毛利率、扣非利润、经营现金流和债务安全之间需要交叉验证。"
    red_flags = outline_list(outline, "red_flags", [
        "若扣非利润弱于归母利润，需警惕非经常性损益掩盖主营压力。",
        "若经营现金流与利润同向恶化，偿债和转型投入弹性会下降。",
        "若存货或应收增长快于收入，收入质量和资产减值风险需要提高权重。",
    ])
    yellow_flags = outline_list(outline, "yellow_flags", [
        "行业价格竞争、产品结构变化和产能利用率会影响毛利率弹性。",
        "资本开支、研发投入和合作模式会影响后续自由现金流。",
    ])
    improvements = outline_list(outline, "improvement_conditions", [
        "收入增长能够传导至毛利率、费用率和扣非利润改善。",
        "经营现金流能够覆盖资本开支和短期债务滚续。",
    ])
    falsifying = outline_list(outline, "falsifying_evidence", [
        "收入增长但现金流转弱、应收和存货同步放大。",
        "短债压力上升且货币资金受限或融资续接能力下降。",
    ])
    observations = outline_list(outline, "observation_items", [
        "持续跟踪收入增速、毛利率、扣非净利润、经营现金流、存货、短债覆盖和治理事项。",
    ])
    peer_count = int(peer_metrics.get("peer_count") or 0)
    peer_strict_ok = bool(peer_metrics.get("strict_ok"))
    peer_interpretation = [
        str(item)
        for item in (peer_metrics.get("interpretation") or [])
        if str(item).strip()
    ]
    peer_missing = [] if peer_strict_ok else [f"peer_metrics_min_3_actual_{peer_count}"]
    peer_selection = peer_metrics.get("selection_method") or "not_built"
    peer_match_status = peer_metrics.get("peer_industry_match_status") or peer_selection
    peer_warnings = [
        str(item)
        for item in peer_metrics.get("peer_selection_warnings", peer_metrics.get("warnings", [])) or []
        if str(item).strip()
    ]
    strategy_items = q_items(qualitative, "strategy", 3)
    product_items = q_items(qualitative, "product_brand", 3)
    operation_items = q_items(qualitative, "operation_driver", 3)
    rd_items = q_items(qualitative, "rd_technology", 2)
    industry_items = q_items(qualitative, "industry_competition", 3)
    external_risk_items = q_items(qualitative, "external_risk", 3)
    target_industry_path = source_industry_path(preflight, snapshot, peer_metrics)
    target_industry_label = industry_label(target_industry_path)
    business_dimension_text = business_dimensions(strategy_items, product_items, operation_items, industry_items, external_risk_items)
    industry_research_items = research_items(industry_research, 5)
    industry_research_missing = research_missing(industry_research)
    governance_items = q_items(qualitative, "governance", 4)
    market_interpretation = market_items(market, 4)
    market_strict_ok = bool(market.get("strict_ok"))
    market_pb = market_value(market, "valuation", "pb")
    market_ps = market_value(market, "valuation", "ps")
    market_pe_status = market_value(market, "valuation", "pe_status")
    market_cap = market_value(market, "market", "market_cap_yi")
    share_price = market_value(market, "market", "share_price")

    sections = [
        make_section(
            "executive_summary",
            [
                core,
                f"报告口径为 {year} 年年度报告，task_id={task_id}。",
                f"核心财务快照：营业收入 {yi(revenue)}，归母净利润 {yi(profit)}，扣非归母净利润 {yi(deducted_profit)}，经营现金流净额 {yi(ocf)}，总资产 {yi(assets)}。",
                f"状态标签：{classify_profit(profit)}、{classify_ocf(ocf)}、{classify_debt(debt_ratio)}。",
                *(strategy_items[:1] or []),
                *(operation_items[:1] or []),
            ],
            [
                f"营业收入同比 {pct(revenue_yoy)}；毛利率 {pct(gross_margin)}；资产负债率 {pct(debt_ratio)}；经营现金流/归母净利润 {fmt(ocf_profit_cover, 'x')}。",
                f"非经常性影响粗略观察：归母净利润-扣非归母净利润={yi(nonrecurring_gap)}，用于判断利润是否依赖非经常项目。",
            ],
            [
                f"核心矛盾：{contradiction}",
                "执行摘要必须把核心矛盾、风险链条和改善条件放在一起，避免只罗列指标。",
                f"定性证据显示，报告解释需要覆盖{business_dimension_text}，而不能只停留在合并报表层面。",
                "当前结论是公开年报财务诊断，不输出目标价、买卖评级或确定性投资建议。",
            ],
            [
                *red_flags[:3],
                *yellow_flags[:2],
                *improvements[:2],
            ],
            evidence_ids(evidence_package, ["operating_revenue", "net_profit_parent", "deducted_parent_net_profit", "net_operating_cash_flow", "total_assets", "total_liabilities"]) + q_evidence(qualitative, ["strategy", "operation_driver"], 4),
            True,
            missing_fields(snapshot, ["operating_revenue", "net_profit_parent", "deducted_parent_net_profit", "net_operating_cash_flow", "total_assets", "total_liabilities"], year) + q_missing(qualitative, ["strategy", "operation_driver"]),
        ),
        make_section(
            "key_changes",
            [
                f"{year} 年营业收入为 {yi(revenue)}，上一年为 {yi(revenue_prev)}，同比 {pct(revenue_yoy)}。",
                f"归母净利润为 {yi(profit)}，扣非归母净利润为 {yi(deducted_profit)}，经营现金流净额为 {yi(ocf)}。",
                f"总资产为 {yi(assets)}，归母净资产为 {yi(parent_equity)}，总负债为 {yi(liabilities)}。",
                *(operation_items[:2] or []),
            ],
            [
                f"关键变化要按改善项、恶化项和观察项归类：收入同比 {pct(revenue_yoy)}，经营现金流同比 {pct(ocf_yoy)}，资产负债率 {pct(debt_ratio)}。",
                f"毛利率 {pct(gross_margin)} 与扣非利润 {yi(deducted_profit)} 共同决定收入增长质量。",
            ],
            [
                "收入增长不能直接等同于盈利质量改善，必须同时验证毛利率、扣非利润和现金流。",
                f"关键变化应拆成{business_dimension_text}等主线，否则无法解释利润弹性。",
                "若利润端与现金流端方向背离，应把变化定义为待验证而非已确认修复。",
            ],
            [
                "改善项优先看毛利率、扣非利润和经营现金流是否同向改善。",
                "恶化项优先看亏损扩大、经营现金流转负、净资产侵蚀和债务覆盖弱化。",
                "观察项包括产品/服务价格、业务量结构、渠道/区域、费用率、存货应收和短债续接。",
            ],
            evidence_ids(evidence_package, ["operating_revenue", "net_profit_parent", "deducted_parent_net_profit", "net_operating_cash_flow", "equity_attributable_parent"]) + q_evidence(qualitative, ["operation_driver"], 3),
            True,
            missing_fields(snapshot, ["operating_revenue", "net_profit_parent", "deducted_parent_net_profit", "net_operating_cash_flow", "equity_attributable_parent"], year) + q_missing(qualitative, ["operation_driver"]),
        ),
        make_section(
            "operating_quality",
            [
                f"公司 {year} 年营业收入为 {yi(revenue)}，同比 {pct(revenue_yoy)}。",
                f"应收账款为 {yi(receivable)}，应收票据为 {yi(notes)}，存货为 {yi(inventory)}，这些项目用于交叉验证收入质量。",
                f"合同负债为 {yi(metric_value(snapshot, 'contract_liabilities', str(year)))}，可作为订单/预收款观察入口，但不能单独证明需求强弱。",
                *(operation_items[:2] or product_items[:2]),
            ],
            [
                f"应收账款+应收票据合计 {yi(receivable_total)}；应收相关项目/营业收入约 {pct(ratio(receivable_total, revenue, 100))}。",
                "收入、回款、应收、存货和合同负债需要放在同一闭环中阅读；只看收入增速会高估经营质量。",
            ],
            [
                "若收入增长伴随应收和存货同步上升，经营质量需要折价；若现金流同步改善，则修复可信度提高。",
                f"{company} 的经营质量还需要把{business_dimension_text}放入解释。",
                "若业务量、订单或项目交付信号改善但全年收入、毛利和现金流未同步修复，应把它定义为经营拐点的早期信号，而不是财务拐点。",
            ],
            [
                "改善条件：收入增长能转化为收现率改善、存货周转改善和合同负债稳定。",
                "风险链：收入增长 -> 应收/库存占用扩大 -> 现金回款弱化 -> 经营现金流承压。",
                "待验证信号：业务量结构、单位价格/客单价、渠道库存、应收账龄和存货跌价准备。",
            ],
            evidence_ids(evidence_package, ["operating_revenue", "accounts_receivable", "notes_receivable", "inventory", "contract_liabilities"]) + q_evidence(qualitative, ["operation_driver", "product_brand"], 4),
            True,
            missing_fields(snapshot, ["operating_revenue", "accounts_receivable", "notes_receivable", "inventory", "contract_liabilities"], year) + q_missing(qualitative, ["operation_driver"]),
        ),
        make_section(
            "profitability_and_cost",
            [
                f"营业收入为 {yi(revenue)}，营业成本为 {yi(cost)}，毛利率为 {pct(gross_margin)}。",
                f"归母净利润为 {yi(profit)}，扣非归母净利润为 {yi(deducted_profit)}，营业利润为 {yi(metric_value(snapshot, 'operating_profit', str(year)))}。",
                f"非经常性影响观察值为 {yi(nonrecurring_gap)}，需要结合政府补助、投资收益、减值等明细解释。",
                *(product_items[:1] or []),
            ],
            [
                f"毛利率=(营业收入-营业成本)/营业收入={pct(gross_margin)}。",
                "杜邦分析在本节降解为净利率、总资产周转率和权益乘数的输入检查；若平均权益或完整同比口径不足，不做伪精确三因子结论。",
                f"收入净利率粗略口径为归母净利润/营业收入={pct(ratio(profit, revenue, 100))}。",
            ],
            [
                "盈利章节必须解释成本成因，而不是只写利润涨跌；重点看价格、产品结构、费用刚性、减值和投资收益。",
                "对于多业务线或集团型公司，需要把各业务线盈利贡献、新产品/新项目投入和周期节奏分开讨论，否则毛利率和净利率的解释会过粗。",
                "扣非利润弱于归母利润时，不能把利润改善直接写成主营修复。",
            ],
            [
                "改善条件：毛利率改善、费用率下降、扣非利润收窄或转正，且非经常性损益占比下降。",
                "风险链：价格竞争或产能利用率不足 -> 毛利率下滑 -> 扣非利润承压 -> 净资产和现金流进一步受损。",
                "待补证据：费用明细、资产减值、政府补助、投资收益和分业务毛利率。",
            ],
            evidence_ids(evidence_package, ["operating_revenue", "operating_cost", "gross_margin", "net_profit_parent", "deducted_parent_net_profit", "operating_profit"]) + q_evidence(qualitative, ["product_brand", "operation_driver"], 4),
            True,
            missing_fields(snapshot, ["operating_revenue", "operating_cost", "net_profit_parent", "deducted_parent_net_profit", "operating_profit"], year) + q_missing(qualitative, ["product_brand"]),
        ),
        make_section(
            "asset_quality_working_capital",
            [
                f"总资产为 {yi(assets)}，货币资金为 {yi(cash)}，存货为 {yi(inventory)}，应收账款为 {yi(receivable)}。",
                f"流动资产为 {yi(current_assets)}，流动负债为 {yi(current_liabilities)}。",
                "资产质量的重点是现金、应收、存货、固定资产/在建工程和长期股权投资能否转化为利润和现金。",
            ],
            [
                f"流动比率=流动资产/流动负债={fmt(current_ratio, 'x')}。",
                f"存货/营业收入约 {pct(ratio(inventory, revenue, 100))}，应收账款/营业收入约 {pct(ratio(receivable, revenue, 100))}。",
                "CCC/DSO/DIO/DPO 需要平均应收、平均存货、营业成本和应付账款口径完整；字段不足时只做方向性提示。",
            ],
            [
                "资产质量的核心不是资产规模，而是这些资产能否转换为利润和现金流。",
                "若存货、应收或开发支出增长快于收入，需要提高减值和回款风险权重。",
            ],
            [
                "改善条件：存货周转改善、应收回款加快、现金占比稳定、资产减值下降。",
                "风险链：库存积压 -> 跌价准备增加 -> 毛利率和利润受损 -> 现金流承压。",
                "待验证信号：应收账龄、存货库龄、跌价准备、受限资金和在建工程转固节奏。",
            ],
            evidence_ids(evidence_package, ["total_assets", "monetary_funds", "inventory", "accounts_receivable", "current_assets", "current_liabilities"]),
            True,
            missing_fields(snapshot, ["total_assets", "monetary_funds", "inventory", "accounts_receivable", "current_assets", "current_liabilities"], year),
        ),
        make_section(
            "debt_liquidity",
            [
                f"总负债为 {yi(liabilities)}，资产负债率为 {pct(debt_ratio)}，货币资金为 {yi(cash)}。",
                f"短期借款为 {yi(short_debt)}，一年内到期非流动负债为 {yi(current_portion)}。",
                f"利息费用为 {yi(interest_expense)}，用于后续利息保障和 Altman 输入校验。",
                f"当前杠杆状态可初步描述为：{classify_debt(debt_ratio)}。",
            ],
            [
                f"现金/短期有息债务约 {fmt(cash_short_debt_cover, 'x')}；短期有息债务口径=短期借款+一年内到期非流动负债。",
                f"流动比率为 {fmt(current_ratio, 'x')}；资产负债率为 {pct(debt_ratio)}。",
                "Altman Z-Score 需要市值、营运资本、留存收益、EBIT、销售收入和总资产等字段；字段缺失时必须明确降级。",
            ],
            [
                "偿债章节需要区分经营性负债和有息债务，不能只凭资产负债率得出安全结论。",
                "现金流、短债覆盖和融资续接能力共同决定债务安全。",
            ],
            [
                "风险链：亏损或现金流转弱 -> 净资产侵蚀 -> 杠杆上升 -> 短债续接压力增加 -> 融资弹性下降。",
                "改善条件：经营现金流持续为正、短债覆盖提高、资产负债率稳定或下降。",
                "复核重点：市值、受限资金、授信、债务到期结构和 EBIT/留存收益口径。",
            ],
            evidence_ids(evidence_package, ["total_liabilities", "total_assets", "monetary_funds", "short_term_borrowings", "current_portion_noncurrent_liabilities", "current_assets", "current_liabilities", "interest_expense"]),
            True,
            missing_fields(snapshot, ["total_liabilities", "total_assets", "monetary_funds", "short_term_borrowings", "current_portion_noncurrent_liabilities", "current_assets", "current_liabilities", "interest_expense"], year) + ["market_cap_for_altman", "ebit_for_altman", "retained_earnings_for_altman"],
        ),
        make_section(
            "cash_flow_quality",
            [
                f"经营现金流净额为 {yi(ocf)}，同比 {pct(ocf_yoy)}。",
                f"归母净利润为 {yi(profit)}，资本开支口径为 {yi(capex)}。",
                f"现金流状态可初步描述为：{classify_ocf(ocf)}。",
            ],
            [
                f"经营现金流/归母净利润={fmt(ocf_profit_cover, 'x')}，用于观察利润现金含量。",
                f"自由现金流=经营现金流-资本开支={yi(fcf_calc)}；资本开支缺失时不得强行计算。",
                f"经营现金流/营业收入={pct(ratio(ocf, revenue, 100))}。",
            ],
            [
                "现金流改善若来自利润改善和回款改善，质量较高；若主要来自应付扩张或压缩付款，则可持续性较弱。",
                "自由现金流是转型投入和债务滚续的关键约束，缺资本开支时必须保留数据缺口。",
            ],
            [
                "改善条件：经营现金流持续为正，并覆盖资本开支和短期债务滚续。",
                "风险链：利润承压 -> 回款放慢或库存增加 -> 经营现金流转弱 -> 自由现金流为负 -> 融资需求上升。",
                "待验证信号：销售收现率、购买商品付现率、资本开支、筹资现金流和票据变化。",
            ],
            evidence_ids(evidence_package, ["net_operating_cash_flow", "net_profit_parent", "capital_expenditure", "operating_revenue"]),
            True,
            missing_fields(snapshot, ["net_operating_cash_flow", "net_profit_parent", "capital_expenditure", "operating_revenue"], year),
        ),
        make_section(
            "industry_competition",
            [
                f"{company} 的行业竞争位置需要结合收入规模、毛利率、费用率、现金流和资产负债率判断。",
                f"目标行业路径：{target_industry_path}；同业样本选择方法为 {peer_selection}，匹配状态为 {peer_match_status}，当前聚合样本数为 {peer_count}。",
                *(peer_interpretation[:4] or ["同业快照未形成有效样本，因此同业结论只能方向性表达。"]),
                *(industry_items[:2] or []),
                *(industry_research_items[:3] or ["Tavily/EXA 外部行业研究未形成有效快照，本节外部行业结论需降级为待核验。"]),
                f"{target_industry_label} 分析需特别关注价格竞争、产品/服务迭代、渠道/区域变化、供应链与政策变量。",
            ],
            [
                f"本公司可用于同业比较的核心指标：收入 {yi(revenue)}、毛利率 {pct(gross_margin)}、经营现金流 {yi(ocf)}、资产负债率 {pct(debt_ratio)}。",
                f"同业门槛：样本数至少 3 家；当前 peer_count={peer_count}，strict_ok={peer_strict_ok}，peer_warnings={', '.join(peer_warnings[:3]) or '无'}。",
            ],
            [
                "本节重点是解释公司相对行业的位置，而不是复述行业背景。",
                "定性证据需要和同业分位合并阅读：如果高附加值业务占比、产品结构或交付节奏改善，但毛利率分位仍低，则竞争位置尚未转化为利润优势。",
                "同业指标已形成时，收入、毛利率、净利率、现金流率和杠杆分位应共同决定竞争位置判断。",
                "Tavily/EXA 外部结果只补充行业趋势和风险触发器，不能替代本地年报财务证据。",
            ],
            [
                "改善条件：毛利率、现金流和收入增速相对同业改善，且不是由一次性因素驱动。",
                "风险链：行业价格竞争延续 -> 单位盈利或毛利空间下降 -> 费用和研发投入刚性 -> 扣非利润承压。",
                "若同业样本仍不足或分类方法过宽，本节结论需要保留方向性降级。",
            ],
            evidence_ids(evidence_package, ["operating_revenue", "gross_margin", "net_operating_cash_flow", "debt_to_asset_ratio"]) + ["peer_metrics:peer_metrics.json"] + q_evidence(qualitative, ["industry_competition", "product_brand", "operation_driver"], 5) + research_evidence(industry_research),
            True,
            missing_fields(snapshot, ["operating_revenue", "gross_margin", "net_operating_cash_flow", "debt_to_asset_ratio"], year) + peer_missing + q_missing(qualitative, ["industry_competition"]) + industry_research_missing,
        ),
        make_section(
            "strategy_policy_external_risk",
            [
                *observations[:2],
                *(strategy_items[:3] or ["产品升级、区域拓展、价格竞争、供应链成本和政策变化最终都要落到收入、毛利率、费用率和现金流。"]),
                *(rd_items[:2] or []),
                *(external_risk_items[:2] or []),
                *(industry_research_items[3:5] or []),
            ],
            [
                "战略有效性需要用业务量/订单、产品结构、毛利率、费用率、资本开支和经营现金流验证。",
                f"当前可直接绑定的财务验证指标包括收入 {yi(revenue)}、毛利率 {pct(gross_margin)}、经营现金流 {yi(ocf)}。",
                "战略定性证据应映射到财务变量：产品/服务价值对应毛利率，组织效能对应费用率和周转，合作生态对应投资收益和资本开支。",
            ],
            [
                "战略表述可信度取决于是否落到财务结果，而不是表述是否积极。",
                "政策利好或合作项目不能直接等同于基本面改善，必须验证订单、毛利和现金流。",
                "若战略变革已带来业务量、订单或产品结构信号，但利润和现金流仍弱，应定义为战略兑现早期阶段，而非完整反转。",
            ],
            [
                "风险链：政策/价格/区域环境变化 -> 业务量或价格承压 -> 毛利率下降 -> 利润和现金流弱化。",
                "改善条件：战略项目带来收入增长，同时毛利率和扣非利润同步改善。",
                "复核重点：管理层承诺兑现、新产品/新项目放量、渠道效率、区域政策和供应链成本。",
            ],
            evidence_ids(evidence_package, ["operating_revenue", "gross_margin", "net_operating_cash_flow"]) + ["semantic:strategy_policy"] + q_evidence(qualitative, ["strategy", "rd_technology", "external_risk"], 6) + research_evidence(industry_research)[:4],
            True,
            missing_fields(snapshot, ["operating_revenue", "gross_margin", "net_operating_cash_flow"], year) + q_missing(qualitative, ["strategy", "external_risk"]) + industry_research_missing,
        ),
        make_section(
            "governance_compliance_shareholders",
            [
                *(governance_items[:4] or ["治理合规章节需要审计意见、关联交易、资金占用、违规担保、问询处罚、质押冻结和股东变动证据。"]),
                "当前生成器不联网查询外部公告，治理判断仅使用已有 wiki/semantic/evidence 检查点。",
                "未发现不等于不存在；外部处罚/问询仍应保留为待核验事项。",
            ],
            [
                "治理风险不做量化打分，采用公开披露事项清单和风险信号清单。",
                "若存在非标审计意见、资金占用或违规担保，应提升为红旗；若只是证据缺失，则进入 review_required。",
            ],
            [
                f"{company} 当前治理判断以关联交易、承诺履行和监管问询等公开披露为核心证据，未补充外部公告前只能给出披露完整性诊断。",
                f"{company} 的治理风险若出现非标审计、资金占用、违规担保或核心股东异常减持，应从观察项升级为估值折价风险。",
            ],
            [
                "风险链：治理或合规异常 -> 信息披露可信度下降 -> 融资能力和估值折价受影响。",
                "改善条件：审计意见标准、无重大问询处罚、无资金占用违规担保、质押减持压力可控。",
                "复核重点：审计报告、交易所问询、行政处罚、关联交易、股东质押/冻结/减持。",
            ],
            ["semantic:governance_compliance", "audit_opinion:review_required", "regulatory_events:review_required"] + q_evidence(qualitative, ["governance"], 6),
            True,
            q_missing(qualitative, ["governance"]) + ["audit_opinion_external_check", "regulatory_inquiry_external_check", "penalty_external_check", "pledge_freeze_external_check"],
        ),
        make_section(
            "valuation_expectation_gap",
            [
                *(market_interpretation or ["当前检查点未包含实时股价、市值、历史估值分位、同业估值分位和一致预期数据。"]),
                f"可用于估值降级判断的基本面锚包括收入 {yi(revenue)}、归母净资产 {yi(parent_equity)}、归母净利润 {yi(profit)}。",
                f"市场快照状态 strict_ok={market_strict_ok}，股价 {fmt(share_price, '元')}，市值 {yi(market_cap)}。",
                "本报告不得输出目标价、买卖评级或精确估值结论。",
            ],
            [
                f"P/B={fmt(market_pb)}，P/S={fmt(market_ps)}；无股价/市值时这两个指标必须保留为未返回。",
                f"P/E 状态为 {market_pe_status or 'missing'}；若净利润为负或扣非利润为负，P/E 应明确不适用或降级。",
            ],
            [
                f"{company} 缺少本地股价和市值快照，市场预期差证据不足；当前估值诊断只能锚定收入 {yi(revenue)}、归母净资产 {yi(parent_equity)} 和归母净利润 {yi(profit)}。",
                f"{company} 收入增长、毛利率 {pct(gross_margin)} 与经营现金流 {yi(ocf)} 的匹配程度，决定估值修复弹性是否具备基本面支撑。",
                f"{company} 若补入市值后 P/B、P/S 或同业分位显著低于基本面锚，才可讨论估值修复弹性；否则应维持风险未充分覆盖的降级判断。",
            ],
            [
                "风险链：主题预期升温但财务修复未兑现 -> 估值波动加大 -> 回撤时基本面支撑不足。",
                "改善条件：补充市值、股价、P/B、P/S、同业分位和一致预期后再讨论估值是否反映风险。",
                "若后续补入本地 market/latest.json 或 manual_snapshot.json，market_snapshot_builder.py 会自动计算 P/B、P/S 和市值锚。",
            ],
            evidence_ids(evidence_package, ["operating_revenue", "net_profit_parent", "equity_attributable_parent"]) + market_evidence(market),
            True,
            missing_fields(snapshot, ["operating_revenue", "net_profit_parent", "equity_attributable_parent"], year) + market_missing(market),
        ),
        make_section(
            "risk_chain_scenario",
            [
                *red_flags[:3],
                *yellow_flags[:2],
                *(industry_items[:1] or []),
                *(external_risk_items[:1] or []),
                *(industry_research_items[:2] or []),
                f"当前核心风险应围绕 {contradiction} 展开。",
            ],
            [
                "风险链条一：收入增长未改善毛利 -> 扣非利润承压 -> 经营现金流弱化 -> 债务续接压力上升 -> 估值折价扩大。",
                "风险链条二：价格竞争/产品结构变化 -> 毛利率下降 -> 费用率刚性放大 -> 盈利修复推迟。",
                "情景推演采用改善、中性、压力三类，不给缺乏依据的概率和精确利润弹性。",
            ],
            [
                "风险章节必须写成触发因素、经营影响、财务报表影响、现金流/债务后果和二级市场含义。",
                "风险链需要把定性触发器纳入：价格竞争、产品结构、区域/海外市场和供应链风险，最终都要落到毛利率、库存、现金流和债务覆盖。",
                "当前更适合做条件跟踪，不适合做单点预测。",
            ],
            [
                "改善情景：毛利率改善、扣非利润收窄或转正、经营现金流覆盖资本开支。",
                "中性情景：收入、订单或业务量改善但毛利率和扣非利润仍需验证。",
                "压力情景：价格竞争延续、毛利率下行、经营现金流转弱、短债覆盖下降。",
                *falsifying[:3],
            ],
            evidence_ids(evidence_package, ["operating_revenue", "gross_margin", "net_profit_parent", "deducted_parent_net_profit", "net_operating_cash_flow", "total_liabilities"]) + q_evidence(qualitative, ["industry_competition", "external_risk", "strategy"], 6) + research_evidence(industry_research)[:4],
            True,
            missing_fields(snapshot, ["operating_revenue", "gross_margin", "net_profit_parent", "deducted_parent_net_profit", "net_operating_cash_flow", "total_liabilities"], year) + q_missing(qualitative, ["external_risk"]) + industry_research_missing,
        ),
        make_section(
            "tracking_checklist",
            [
                "后续跟踪的价值在于把当前判断改写成可验证指标，而不是复述风险。",
                f"当前重点跟踪：收入 {yi(revenue)}、毛利率 {pct(gross_margin)}、扣非归母净利润 {yi(deducted_profit)}、经营现金流 {yi(ocf)}。",
                *observations[:2],
            ],
            [
                "核心跟踪指标：收入增速、毛利率、扣非归母净利润、经营现金流、自由现金流、资产负债率、现金/短债覆盖。",
                f"改善阈值示例：经营现金流持续为正且自由现金流改善；当前自由现金流为 {yi(fcf_calc)}。",
                f"恶化阈值示例：毛利率继续下降、经营现金流转负、现金/短债覆盖低于关键安全区；当前现金/短债覆盖 {fmt(cash_short_debt_cover, 'x')}。",
            ],
            [
                "跟踪清单要服务于推翻或确认当前结论，不能只列数据源。",
                "频率应按月度经营数据/公告、季度财报、年度报告和监管事件分层。",
                "定性跟踪还应覆盖新产品/服务推出节奏、核心业务线恢复、高附加值业务占比、渠道/区域拓展和组织变革兑现。",
            ],
            [
                *improvements[:2],
                *falsifying[:2],
                "数据源：定期报告、月度经营数据、交易所公告、监管问询、行业同业指标和市场估值快照。",
            ],
            evidence_ids(evidence_package, ["operating_revenue", "gross_margin", "deducted_parent_net_profit", "net_operating_cash_flow", "capital_expenditure", "total_liabilities"]) + q_evidence(qualitative, ["strategy", "product_brand", "operation_driver"], 5),
            True,
            missing_fields(snapshot, ["operating_revenue", "gross_margin", "deducted_parent_net_profit", "net_operating_cash_flow", "capital_expenditure", "total_liabilities"], year) + q_missing(qualitative, ["strategy", "operation_driver"]),
        ),
        make_section(
            "data_quality_traceability",
            [
                f"预检状态：artifact={preflight.get('artifact_status', '未返回')}，postgres={preflight.get('postgres_status', '未返回')}，evidence={preflight.get('evidence_status', '未返回')}。",
                f"报告使用 metric_snapshot.json、evidence_package.json 和 analysis_outline.json 生成 section_drafts.json，task_id={task_id}。",
                "所有缺失字段必须显式写入 missing_fields 或 review_required，不能让 renderer 静默补成泛泛段落。",
            ],
            [
                "数据质量检查包括三表钩稽、单位换算、核心指标证据、模型字段完整性、同业样本、定性证据和市场估值数据。",
                "Altman Z-Score、CCC、自由现金流、估值分位和同业比较必须在字段不足时明确降级。",
                f"定性证据快照 bucket_count={qualitative.get('bucket_count', 0)}，evidence_count={qualitative.get('evidence_count', 0)}。",
                f"市场估值快照 strict_ok={market_strict_ok}，warnings={', '.join(str(item) for item in market.get('warnings', []) or []) or '无'}。",
                f"Tavily/EXA 行业研究 strict_ok={bool(industry_research.get('strict_ok'))}，external_result_count={industry_research.get('external_result_count', 0)}。",
                f"本次 schema 校验要求固定 14 章，字段完整，且每章 evidence_ids 不为空。",
            ],
            [
                "数据质量是报告可信度的底座，无法判断事项需要集中披露。",
                "如果某章节只有 missing 证据或缺少核心字段，最终报告应保留人工复核提示。",
            ],
            [
                "人工复核：毛利率、资本开支、短债覆盖、同业样本、定性证据、估值市场数据、治理合规原文证据。",
                "改善条件：补齐 peer_metrics、qualitative_snapshot、market_snapshot、governance evidence 和 citation repair 后再进入严格验收。",
                "本报告为公开信息财务诊断，不构成投资建议。",
            ],
            evidence_ids(evidence_package, ["operating_revenue", "net_profit_parent", "net_operating_cash_flow", "total_assets", "total_liabilities"]) + ["qualitative_snapshot:qualitative_snapshot.json", "market_snapshot:market_snapshot.json", "industry_research:industry_research.json"],
            True,
            missing_fields(snapshot, ["operating_revenue", "net_profit_parent", "net_operating_cash_flow", "total_assets", "total_liabilities"], year) + ([] if market_strict_ok else ["market_snapshot_price_or_market_cap"]) + industry_research_missing,
        ),
    ]
    return sections


def build_quality_report(sections: list[dict[str, Any]], validation: dict[str, Any]) -> dict[str, Any]:
    review_queue: list[str] = []
    for section in sections:
        missing = section.get("missing_fields")
        if isinstance(missing, list) and missing:
            review_queue.append(f"{section['section_id']}: 缺失字段 {', '.join(str(item) for item in missing[:8])}")
    for warning in validation.get("warnings", []) or []:
        review_queue.append(str(warning))
    return {
        "schema": "section_drafts.schema.json",
        "module_count": len(sections),
        "section_order_valid": [s["section_id"] for s in sections] == SECTION_IDS,
        "all_sections_have_required_fields": validation.get("ok", False),
        "sections_with_review_required": [s["section_id"] for s in sections if s.get("review_required")],
        "review_queue": review_queue[:60],
        "validation": validation,
        "generated_by": "generate_section_drafts.py",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--output", type=Path, help="Default: <work-dir>/section_drafts.json")
    parser.add_argument("--write-validation", type=Path, help="Default: <work-dir>/section_drafts_validation.json")
    args = parser.parse_args()

    work_dir = args.work_dir
    preflight = load_json_if_exists(work_dir / "preflight.json")
    snapshot = load_json_if_exists(work_dir / "metric_snapshot.json")
    evidence_package = load_json_if_exists(work_dir / "evidence_package.json")
    outline = load_json_if_exists(work_dir / "analysis_outline.json")
    peer_metrics = load_json_if_exists(work_dir / "peer_metrics.json")
    qualitative = load_json_if_exists(work_dir / "qualitative_snapshot.json")
    industry_research = load_json_if_exists(work_dir / "industry_research.json")
    market = load_json_if_exists(work_dir / "market_snapshot.json")
    wiki_inventory = load_json_if_exists(work_dir / "wiki_inventory.json")
    company_dir = company_dir_from_inventory(wiki_inventory)
    if not snapshot:
        print(json.dumps({"ok": False, "stage": "missing_metric_snapshot", "work_dir": str(work_dir)}, ensure_ascii=False, indent=2))
        return 2
    if not evidence_package:
        print(json.dumps({"ok": False, "stage": "missing_evidence_package", "work_dir": str(work_dir)}, ensure_ascii=False, indent=2))
        return 2
    evidence_package = normalize_evidence_package(
        evidence_package,
        snapshot=snapshot,
        lookup=load_provenance_lookup(company_dir, args.year),
        default_task_id=preflight.get("task_id") or snapshot.get("task_id"),
        year=args.year,
        aliases=ALIASES,
    )
    dump_json(work_dir / "evidence_package.json", evidence_package)

    sections = build_sections(preflight, snapshot, evidence_package, outline, peer_metrics, qualitative, industry_research, market, args.year)
    data = {
        "schema_version": 1,
        "schema": str(SECTION_SCHEMA_JSON),
        "generated_by": "generate_section_drafts.py",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "company_id": preflight.get("company_id") or snapshot.get("company_id"),
        "report_year": args.year,
        "wiki_inventory": {
            "source": str(work_dir / "wiki_inventory.json"),
            "file_count": wiki_inventory.get("file_count"),
            "missing_required_files": wiki_inventory.get("missing_required_files", []),
            "read_scope": wiki_inventory.get("read_scope"),
            "postgres_role": wiki_inventory.get("postgres_role"),
        },
        "sections": sections,
    }
    validation = validate_section_drafts(data)
    data["quality_report"] = build_quality_report(sections, validation)

    output = args.output or work_dir / "section_drafts.json"
    validation_path = args.write_validation or work_dir / "section_drafts_validation.json"
    dump_json(output, data)
    dump_json(validation_path, validation)

    result = {
        "ok": validation["ok"],
        "stage": "completed" if validation["ok"] else "schema_validation_failed",
        "output": str(output),
        "validation": validation,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if validation["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
