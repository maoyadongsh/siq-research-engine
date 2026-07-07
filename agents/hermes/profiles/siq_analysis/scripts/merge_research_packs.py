#!/usr/bin/env python3
"""Merge SIQ research packs into section_drafts without changing the renderer contract."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


SECTION_BLOCK_TITLES = {
    "executive_summary": "综合补证判断",
    "key_changes": "关键变化补证",
    "operating_quality": "经营质量补证",
    "profitability_and_cost": "盈利与成本补证",
    "asset_quality_working_capital": "资产质量补证",
    "debt_liquidity": "偿债安全补证",
    "cash_flow_quality": "现金流补证",
    "industry_competition": "行业同业补证",
    "strategy_policy_external_risk": "战略与外部变量补证",
    "governance_compliance_shareholders": "治理风险补证",
    "valuation_expectation_gap": "估值锚补证",
    "risk_chain_scenario": "风险链补证",
    "tracking_checklist": "跟踪信号补证",
    "data_quality_traceability": "证据质量补证",
}

FACT_BLOCK_TITLES = {
    "executive_summary": "本地事实证据补强",
    "key_changes": "本地指标变化证据",
    "operating_quality": "本地经营证据补强",
    "profitability_and_cost": "本地盈利证据补强",
    "asset_quality_working_capital": "本地资产质量证据",
    "debt_liquidity": "本地偿债证据补强",
    "cash_flow_quality": "本地现金流证据补强",
    "industry_competition": "本地同业证据补强",
    "strategy_policy_external_risk": "本地战略证据补强",
    "governance_compliance_shareholders": "本地治理证据补强",
    "valuation_expectation_gap": "本地估值锚证据",
    "risk_chain_scenario": "本地风险证据补强",
    "tracking_checklist": "本地跟踪证据补强",
    "data_quality_traceability": "本地溯源证据补强",
}

CALCULATION_BLOCK_TITLES = {
    "executive_summary": "模型与口径补强",
    "key_changes": "变化幅度测算",
    "operating_quality": "经营效率测算",
    "profitability_and_cost": "利润弹性测算",
    "asset_quality_working_capital": "营运资本测算",
    "debt_liquidity": "偿债覆盖测算",
    "cash_flow_quality": "现金流质量测算",
    "industry_competition": "同业分位测算",
    "strategy_policy_external_risk": "战略兑现测算",
    "governance_compliance_shareholders": "治理影响测算",
    "valuation_expectation_gap": "估值锚测算",
    "risk_chain_scenario": "情景触发测算",
    "tracking_checklist": "跟踪阈值测算",
    "data_quality_traceability": "数据质量测算",
}

RISK_BLOCK_TITLES = {
    "executive_summary": "风险链与反证条件",
    "key_changes": "变化背后的风险链",
    "operating_quality": "经营质量风险链",
    "profitability_and_cost": "盈利压力传导",
    "asset_quality_working_capital": "资产周转风险链",
    "debt_liquidity": "偿债风险链",
    "cash_flow_quality": "现金流风险链",
    "industry_competition": "行业竞争风险链",
    "strategy_policy_external_risk": "政策与外部变量链",
    "governance_compliance_shareholders": "治理风险链",
    "valuation_expectation_gap": "预期差风险链",
    "risk_chain_scenario": "核心风险链条",
    "tracking_checklist": "需要跟踪的风险触发器",
    "data_quality_traceability": "证据缺口风险链",
}

TRACKING_BLOCK_TITLES = {
    "executive_summary": "后续验证信号",
    "key_changes": "变化验证信号",
    "operating_quality": "经营质量跟踪",
    "profitability_and_cost": "盈利修复跟踪",
    "asset_quality_working_capital": "营运资本跟踪",
    "debt_liquidity": "流动性跟踪",
    "cash_flow_quality": "现金流跟踪",
    "industry_competition": "行业同业跟踪",
    "strategy_policy_external_risk": "战略兑现跟踪",
    "governance_compliance_shareholders": "治理事项跟踪",
    "valuation_expectation_gap": "估值预期跟踪",
    "risk_chain_scenario": "情景验证信号",
    "tracking_checklist": "研究跟踪清单",
    "data_quality_traceability": "数据复核清单",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"json_root_not_object:{path}")
    return data


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_packs(research_packs_dir: Path) -> list[dict[str, Any]]:
    packs: list[dict[str, Any]] = []
    for path in sorted(research_packs_dir.glob("*.json")):
        pack = load_json(path)
        pack["_pack_file"] = str(path)
        packs.append(pack)
    return packs


def clean_text(value: Any, limit: int | None = 220) -> str:
    text = " ".join(str(value or "").split())
    if limit is None or len(text) <= limit:
        return text
    cut = max(text.rfind("。", 0, limit), text.rfind("；", 0, limit), text.rfind("，", 0, limit))
    if cut >= max(30, limit // 3):
        return text[: cut + 1].rstrip()
    return text[:limit].rstrip("，。；;、 ")


def visible_text(value: Any) -> str:
    """Normalize visible report text without adding artificial ellipses."""
    return clean_text(value, None)


KNOWN_SECTION_IDS = set(SECTION_BLOCK_TITLES)
SOURCE_LABEL_PREFIX_RE = re.compile(r"^【[^】]+】")


def strip_source_label(text: str) -> str:
    return SOURCE_LABEL_PREFIX_RE.sub("", str(text or "")).strip()


def compress_for_synthesis(text: str, limit: int = 180) -> str:
    clean = strip_source_label(text)
    clean = re.sub(r"「[^」]{80,}」", "「年报战略/业务原文摘录」", clean)
    clean = re.sub(r"；证据：[^（]+", "", clean)
    clean = re.sub(r"（来源：[^）]+）", "", clean)
    clean = " ".join(clean.split())
    return clean


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def section_ids_from_values(values: Any) -> list[str]:
    section_ids: list[str] = []
    for value in as_list(values):
        sid = str(value).strip()
        if sid in KNOWN_SECTION_IDS and sid not in section_ids:
            section_ids.append(sid)
    return section_ids


def coverage_section_ids(pack: dict[str, Any]) -> list[str]:
    coverage = pack.get("coverage")
    if not isinstance(coverage, dict):
        return []
    return section_ids_from_values(coverage.get("section_ids"))


def section_ids_from_scope(scope: Any) -> list[str]:
    text = str(scope or "")
    section_ids: list[str] = []
    for sid in KNOWN_SECTION_IDS:
        if sid in text and sid not in section_ids:
            section_ids.append(sid)
    return section_ids


def infer_financial_sections(text: str) -> list[str]:
    mapping = [
        (("营业收入", "收入", "营收", "operating_revenue", "revenue"), ["executive_summary", "key_changes", "operating_quality"]),
        (("毛利率", "营业成本", "成本", "费用率", "gross_margin", "operating_cost"), ["profitability_and_cost", "key_changes"]),
        (("归母净利润", "扣非", "净利润", "利润", "盈利", "parent_net_profit", "deducted_parent_net_profit", "net_profit"), ["profitability_and_cost", "executive_summary", "key_changes"]),
        (("经营现金流", "自由现金流", "现金流", "operating_cash_flow", "free_cash_flow"), ["cash_flow_quality", "executive_summary", "key_changes"]),
        (("总资产", "资产总计", "存货", "应收", "营运资本", "资产", "total_assets", "inventory", "accounts_receivable"), ["asset_quality_working_capital", "key_changes"]),
        (("总负债", "负债合计", "负债", "短债", "负债率", "偿债", "货币资金", "流动性", "total_liabilities", "debt", "liability"), ["debt_liquidity", "executive_summary"]),
        (("估值", "市值", "股价", "P/B", "P/S", "P/E", "EPS"), ["valuation_expectation_gap"]),
        (("同业", "分位", "peer", "行业", "peer_metrics"), ["industry_competition", "valuation_expectation_gap"]),
    ]
    section_ids: list[str] = []
    for keywords, targets in mapping:
        if any(keyword in text for keyword in keywords):
            for sid in targets:
                if sid not in section_ids:
                    section_ids.append(sid)
    return section_ids


def item_section_ids(pack: dict[str, Any], item: dict[str, Any], text: str = "") -> list[str]:
    explicit = section_ids_from_values(item.get("section_ids"))
    if explicit:
        return explicit
    scoped = section_ids_from_scope(item.get("scope"))
    if scoped:
        return scoped
    inferred = infer_financial_sections(text)
    if inferred:
        return inferred
    return coverage_section_ids(pack)


def evidence_refs_summary(item: dict[str, Any], limit: int = 2) -> str:
    refs = item.get("evidence_refs")
    if not isinstance(refs, list):
        return ""
    parts: list[str] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        source = clean_text(ref.get("source_file"), 80)
        if not source:
            continue
        extra: list[str] = []
        if ref.get("pdf_page") not in (None, "", "未返回"):
            extra.append(f"p{ref.get('pdf_page')}")
        if ref.get("table_index") not in (None, "", "未返回"):
            extra.append(f"t{ref.get('table_index')}")
        parts.append(f"{source}{' ' + '/'.join(extra) if extra else ''}")
        if len(parts) >= limit:
            break
    return "；证据：" + "、".join(parts) if parts else ""


def merge_item_metadata(item: dict[str, Any], agent_id: str) -> dict[str, Any]:
    metadata: dict[str, Any] = {"agent_id": agent_id}
    for key in ("confidence", "review_required", "fact_status"):
        if key in item:
            metadata[key] = item.get(key)
    return metadata


def block_item_metadata(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item[key]
        for key in ("agent_id", "confidence", "review_required", "fact_status")
        if key in item
    }


def fact_source_label(item: dict[str, Any]) -> str:
    scope = str(item.get("scope") or "").lower()
    refs = item.get("evidence_refs")
    source_files: list[str] = []
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, dict):
                source_files.append(str(ref.get("source_file") or "").lower())
    joined_sources = " ".join(source_files)
    external_scope_tokens = [
        "cross_market_reference",
        "industry_trend",
        "technology_barrier",
        "rd_patent",
        "mass_production",
        "supply_chain_position",
        "external_search",
    ]
    if any(token in joined_sources for token in ["industry_research", "tavily", "exa", "http"]):
        return "【外部补充事实】"
    if any(token in scope for token in external_scope_tokens):
        return "【外部补充事实】"
    return "【本地事实证据】"


def format_fact(item: dict[str, Any], agent_id: str) -> str:
    fact = visible_text(item.get("fact"))
    if not fact:
        return ""
    return f"{fact_source_label(item)}{fact}{evidence_refs_summary(item)}（来源：{agent_id}）"


def fmt_number(value: Any) -> str:
    if isinstance(value, (int, float)):
        abs_value = abs(value)
        if abs_value >= 100:
            return f"{value:.2f}"
        if abs_value >= 10:
            return f"{value:.2f}"
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return clean_text(value, 120)


def summarize_peer_aggregates(output: dict[str, Any]) -> str:
    labels = {
        "operating_revenue_yi": "收入规模",
        "operating_revenue_yoy_pct": "收入增速",
        "gross_margin_pct": "毛利率",
        "parent_net_profit_yi": "归母净利润",
        "operating_cash_flow_net_yi": "经营现金流",
        "debt_to_asset_ratio_pct": "资产负债率",
    }
    parts: list[str] = []
    for key, label in labels.items():
        item = output.get(key)
        if not isinstance(item, dict):
            continue
        sample_count = item.get("sample_count")
        target = item.get("target_value")
        percentile = item.get("target_percentile")
        median = item.get("median")
        if target is None and percentile is None and median is None:
            continue
        pieces = []
        if target is not None:
            pieces.append(f"目标值 {fmt_number(target)}")
        if percentile is not None:
            pieces.append(f"约处于 {fmt_number(percentile)} 分位")
        if median is not None:
            pieces.append(f"同业中位 {fmt_number(median)}")
        if sample_count is not None:
            pieces.append(f"样本 {sample_count} 家")
        parts.append(f"{label}：" + "，".join(pieces))
        if len(parts) >= 4:
            break
    return "；".join(parts)


def summarize_output(output: Any) -> str:
    if isinstance(output, dict):
        peer_summary = summarize_peer_aggregates(output)
        if peer_summary:
            return peer_summary
        priority_keys = [
            "value_pct",
            "value_x",
            "value",
            "period",
            "unit",
            "reliability",
            "interpretation",
            "strategy_read",
            "downgrade_reason",
        ]
        parts: list[str] = []
        labels = {
            "value_pct": "数值",
            "value_x": "倍数",
            "value": "数值",
            "period": "期间",
            "unit": "单位",
            "reliability": "可靠性",
            "interpretation": "解释",
            "strategy_read": "战略解读",
            "downgrade_reason": "降级原因",
        }
        for key in priority_keys:
            if key not in output or output.get(key) in (None, ""):
                continue
            value = output.get(key)
            if key == "value_pct" and isinstance(value, (int, float)):
                value_text = f"{value:.2f}%"
            elif key == "value_x" and isinstance(value, (int, float)):
                value_text = f"{value:.2f}x"
            else:
                value_text = fmt_number(value)
            parts.append(f"{labels.get(key, key)}={value_text}")
        if parts:
            return "；".join(parts[:5])
        compact = json.dumps(output, ensure_ascii=False, sort_keys=True)
        return visible_text(compact)
    if isinstance(output, list):
        return "；".join(visible_text(item) for item in output[:4])
    return fmt_number(output)


def format_calculation(item: dict[str, Any], agent_id: str) -> str:
    name = clean_text(item.get("name"), 90)
    formula = clean_text(item.get("formula"), 120)
    output = summarize_output(item.get("output"))
    if not name and not output:
        return ""
    prefix = name or "模型输出"
    formula_part = f"；口径：{formula}" if formula else ""
    return f"【模型测算】{prefix}：{output}{formula_part}{evidence_refs_summary(item)}（来源：{agent_id}）"


def format_risk_chain(item: dict[str, Any], agent_id: str) -> str:
    chain = [visible_text(value) for value in as_list(item.get("chain")) if visible_text(value)]
    if not chain:
        return ""
    severity = clean_text(item.get("severity"), 40) or "unknown"
    text = " -> ".join(chain[:5])
    counters = [visible_text(value) for value in as_list(item.get("counter_signals")) if visible_text(value)]
    counter_text = f"；反证/缓释：{'；'.join(counters[:2])}" if counters else ""
    return f"【风险链】{severity}：{text}{counter_text}{evidence_refs_summary(item)}（来源：{agent_id}）"


def format_tracking_signal(item: dict[str, Any], agent_id: str) -> str:
    signal = visible_text(item.get("signal"))
    if not signal:
        return ""
    direction = clean_text(item.get("direction"), 40)
    why = visible_text(item.get("why_it_matters"))
    source_hint = visible_text(item.get("source_hint"))
    parts = [signal]
    if direction:
        parts.append(f"方向={direction}")
    if why:
        parts.append(why)
    if source_hint:
        parts.append(f"来源提示={source_hint}")
    return "【跟踪信号】" + "；".join(parts) + f"（来源：{agent_id}）"


def format_external_source(item: dict[str, Any], agent_id: str) -> str:
    provider = clean_text(item.get("provider"), 40)
    title = clean_text(item.get("title"), 140)
    query = clean_text(item.get("query"), 120)
    reliability = clean_text(item.get("reliability"), 40)
    if not title and not query:
        return ""
    parts = []
    if provider:
        parts.append(provider)
    if title:
        parts.append(f"《{title}》")
    if query:
        parts.append(f"检索式：{query}")
    if reliability:
        parts.append(f"可靠性={reliability}")
    return "【外部搜索补证】" + "；".join(parts) + f"（来源：{agent_id}，仅作外部来源索引，不覆盖本地年报事实）"


def finding_label(finding: dict[str, Any], agent_id: str) -> str:
    refs = finding.get("evidence_refs")
    source_files: list[str] = []
    if isinstance(refs, list):
        for ref in refs:
            if isinstance(ref, dict):
                source_files.append(str(ref.get("source_file") or ""))
    joined = " ".join(source_files)
    if "industry_research" in joined:
        return "【外部搜索补证形成的判断】"
    if "peer_metrics" in joined:
        return "【本地同业模型判断】"
    if agent_id == "evidence_curator":
        return "【证据状态判断】"
    return "【基于本地证据的分析判断】"


def collect_findings_by_section(packs: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    by_section: dict[str, list[dict[str, str]]] = {}
    for pack in packs:
        agent_id = str(pack.get("agent_id") or "unknown_agent")
        for finding in pack.get("key_findings", []) or []:
            if not isinstance(finding, dict):
                continue
            claim = visible_text(finding.get("claim"))
            if not claim:
                continue
            section_ids = finding.get("section_ids")
            if not isinstance(section_ids, list):
                continue
            if agent_id == "evidence_curator" and len(section_ids) > 10:
                section_ids = ["executive_summary", "data_quality_traceability"]
            for section_id in section_ids:
                sid = str(section_id)
                by_section.setdefault(sid, []).append({
                    "agent_id": agent_id,
                    "claim": f"{finding_label(finding, agent_id)}{claim}",
                    **merge_item_metadata(finding, agent_id),
                })
    return by_section


def collect_missing_by_section(packs: list[dict[str, Any]]) -> dict[str, list[str]]:
    by_section: dict[str, list[str]] = {}
    for pack in packs:
        agent_id = str(pack.get("agent_id") or "unknown_agent")
        for item in pack.get("missing_inputs", []) or []:
            if not isinstance(item, dict):
                continue
            name = clean_text(item.get("name"), 120)
            reason = clean_text(item.get("reason"), 160)
            section_ids = item.get("section_ids")
            if not isinstance(section_ids, list):
                continue
            marker = f"{agent_id}:{name}:{reason}" if reason else f"{agent_id}:{name}"
            if agent_id == "evidence_curator" and name == "wiki_required_files" and len(section_ids) > 10:
                section_ids = ["executive_summary", "data_quality_traceability"]
            for section_id in section_ids:
                by_section.setdefault(str(section_id), []).append(marker)
    return by_section


def append_by_section(
    by_section: dict[str, list[dict[str, str]]],
    section_ids: list[str],
    item: str,
    agent_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    text = visible_text(item)
    if not text:
        return
    payload: dict[str, Any] = {"agent_id": agent_id, "text": text}
    if metadata:
        payload.update(metadata)
    for section_id in section_ids:
        if section_id in KNOWN_SECTION_IDS:
            by_section.setdefault(section_id, []).append(payload)


def collect_pack_items_by_section(packs: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, str]]]]:
    grouped: dict[str, dict[str, list[dict[str, str]]]] = {
        "facts": {},
        "calculations": {},
        "risks": {},
        "tracking": {},
        "external_sources": {},
    }
    for pack in packs:
        agent_id = str(pack.get("agent_id") or "unknown_agent")
        for fact in as_list(pack.get("evidence_facts")):
            if not isinstance(fact, dict):
                continue
            text = format_fact(fact, agent_id)
            append_by_section(grouped["facts"], item_section_ids(pack, fact, text), text, agent_id, merge_item_metadata(fact, agent_id))
        for calc in as_list(pack.get("calculations")):
            if not isinstance(calc, dict):
                continue
            text = format_calculation(calc, agent_id)
            section_ids = item_section_ids(pack, calc, " ".join([text, str(calc.get("name") or "")]))
            append_by_section(grouped["calculations"], section_ids, text, agent_id, merge_item_metadata(calc, agent_id))
        for risk in as_list(pack.get("risk_chains")):
            if not isinstance(risk, dict):
                continue
            text = format_risk_chain(risk, agent_id)
            append_by_section(grouped["risks"], item_section_ids(pack, risk, text), text, agent_id, merge_item_metadata(risk, agent_id))
        for signal in as_list(pack.get("tracking_signals")):
            if not isinstance(signal, dict):
                continue
            text = format_tracking_signal(signal, agent_id)
            append_by_section(grouped["tracking"], item_section_ids(pack, signal, text), text, agent_id, merge_item_metadata(signal, agent_id))
        for source in as_list(pack.get("external_sources")):
            if not isinstance(source, dict):
                continue
            text = format_external_source(source, agent_id)
            section_ids = ["industry_competition", "strategy_policy_external_risk", "valuation_expectation_gap"]
            append_by_section(grouped["external_sources"], section_ids, text, agent_id)
    return grouped


def dedupe(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = visible_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def collect_agents_for_section(
    section_id: str,
    findings_by_section: dict[str, list[dict[str, str]]],
    item_groups: dict[str, dict[str, list[dict[str, str]]]],
    missing_by_section: dict[str, list[str]],
) -> list[str]:
    agents: set[str] = set()
    for item in findings_by_section.get(section_id, []):
        if item.get("agent_id"):
            agents.add(str(item["agent_id"]))
    for group in item_groups.values():
        for item in group.get(section_id, []):
            if item.get("agent_id"):
                agents.add(str(item["agent_id"]))
    for item in missing_by_section.get(section_id, []):
        if ":" in item:
            agents.add(item.split(":", 1)[0])
    return sorted(agents)


def dedupe_group_items(items: list[dict[str, str]], limit: int) -> list[str]:
    return dedupe([item.get("text", "") for item in items], limit)


def dedupe_entries(items: list[dict[str, Any]], text_key: str, limit: int) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        text = visible_text(item.get(text_key))
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(item)
        if len(result) >= limit:
            break
    return result


def block_metadata_entries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [block_item_metadata(item) for item in items]


def metadata_requires_review(items: list[dict[str, Any]]) -> bool:
    review_statuses = {"assumption", "gap", "modeled_estimate"}
    for item in items:
        if item.get("review_required") is True:
            return True
        if str(item.get("fact_status") or "") in review_statuses:
            return True
    return False


def build_research_synthesis_items(
    section_id: str,
    claims: list[str],
    facts: list[str],
    calculations: list[str],
    risks: list[str],
    tracking: list[str],
    external_sources: list[str],
) -> list[str]:
    local_claim = compress_for_synthesis(claims[0], 190) if claims else ""
    local_fact = compress_for_synthesis(facts[0], 160) if facts else ""
    model = compress_for_synthesis(calculations[0], 170) if calculations else ""
    risk = compress_for_synthesis(risks[0], 180) if risks else ""
    tracking_signal = compress_for_synthesis(tracking[0], 160) if tracking else ""
    external = compress_for_synthesis(external_sources[0], 180) if external_sources else ""

    items: list[str] = []
    if local_claim or local_fact or model:
        pieces = []
        if local_claim:
            pieces.append(f"本节结论是：{local_claim}")
        if local_fact:
            pieces.append(f"证据基础来自本地年报和指标：{local_fact}")
        if model:
            pieces.append(f"模型层面的验证结果是：{model}")
        items.append("；".join(pieces).rstrip("。") + "。")
    if external:
        items.append(
            f"外部上下文提示：{external} 这类信息用于解释行业、政策、技术或竞争变量，最终仍需与本地年报、同业模型和财务变量交叉验证。"
        )
    if risk or tracking_signal:
        pieces = []
        if risk:
            pieces.append(f"主要风险链条是：{risk}")
        if tracking_signal:
            pieces.append(f"后续验证信号包括：{tracking_signal}")
        items.append("；".join(pieces).rstrip("。") + "。")

    return dedupe([visible_text(item) for item in items], 3)


def append_block(
    section: dict[str, Any],
    title: str,
    role: str,
    items: list[str],
    item_metadata: list[dict[str, Any]] | None = None,
) -> bool:
    clean_items = dedupe(items, len(items))
    if not clean_items:
        return False
    blocks = section.setdefault("narrative_blocks", [])
    if not isinstance(blocks, list):
        blocks = []
        section["narrative_blocks"] = blocks
    block = {
        "title": title,
        "role": role,
        "source": "research_pack_merge",
        "items": clean_items,
    }
    if item_metadata:
        block["item_metadata"] = item_metadata[: len(clean_items)]
    blocks.append(block)
    return True


def remove_stale_pack_merge_content(section: dict[str, Any]) -> bool:
    changed = False
    blocks = section.get("narrative_blocks")
    if not isinstance(blocks, list):
        blocks = []
        section["narrative_blocks"] = blocks
        changed = True
    original_count = len(blocks)
    blocks[:] = [
        block for block in blocks
        if not (isinstance(block, dict) and block.get("source") == "research_pack_merge")
    ]
    changed = changed or len(blocks) != original_count

    judgements = section.get("judgements")
    if isinstance(judgements, list):
        original = list(judgements)
        judgements[:] = [
            item for item in judgements
            if not (isinstance(item, str) and "（来源：" in item)
        ]
        changed = changed or judgements != original

    missing_fields = section.get("missing_fields")
    if isinstance(missing_fields, list):
        original = list(missing_fields)
        missing_fields[:] = [
            item for item in missing_fields
            if not (isinstance(item, str) and item.startswith("research_pack:"))
        ]
        changed = changed or missing_fields != original
    return changed


def merge_legacy_array(section: dict[str, Any], field: str, items: list[str], limit: int) -> bool:
    if not items:
        return False
    values = section.get(field)
    if not isinstance(values, list):
        values = []
        section[field] = values
    changed = False
    for item in dedupe(items, limit):
        if item not in values:
            values.append(item)
            changed = True
    return changed


def merge_section(
    section: dict[str, Any],
    findings_by_section: dict[str, list[dict[str, str]]],
    item_groups: dict[str, dict[str, list[dict[str, str]]]],
    missing_by_section: dict[str, list[str]],
) -> bool:
    section_id = str(section.get("section_id") or "")
    findings = findings_by_section.get(section_id, [])
    facts = item_groups["facts"].get(section_id, [])
    calculations = item_groups["calculations"].get(section_id, [])
    risks = item_groups["risks"].get(section_id, [])
    tracking = item_groups["tracking"].get(section_id, [])
    external_sources = item_groups["external_sources"].get(section_id, [])
    missing = missing_by_section.get(section_id, [])
    changed = remove_stale_pack_merge_content(section)

    claim_entries = []
    for item in dedupe_entries(findings, "claim", 4):
        claim_entries.append({**item, "text": f"{item['claim']}（来源：{item['agent_id']}）"})
    claims = [entry["text"] for entry in claim_entries]
    changed = append_block(
        section,
        SECTION_BLOCK_TITLES.get(section_id, "补充证据"),
        "evidence",
        claims,
        block_metadata_entries(claim_entries),
    ) or changed

    fact_entries = dedupe_entries(facts, "text", 4)
    calculation_entries = dedupe_entries(calculations, "text", 4)
    risk_entries = dedupe_entries(risks, "text", 4)
    tracking_entries = dedupe_entries(tracking, "text", 4)
    external_source_entries = dedupe_entries(external_sources, "text", 3)
    fact_items = [entry["text"] for entry in fact_entries]
    calculation_items = [entry["text"] for entry in calculation_entries]
    risk_items = [entry["text"] for entry in risk_entries]
    tracking_items = [entry["text"] for entry in tracking_entries]
    external_source_items = [entry["text"] for entry in external_source_entries]

    synthesis_items = build_research_synthesis_items(
        section_id,
        claims,
        fact_items,
        calculation_items,
        risk_items,
        tracking_items,
        external_source_items,
    )
    changed = append_block(section, "研究包融合解读", "synthesis", synthesis_items) or changed
    changed = append_block(section, FACT_BLOCK_TITLES.get(section_id, "证据事实"), "evidence", fact_items, block_metadata_entries(fact_entries)) or changed
    changed = append_block(section, CALCULATION_BLOCK_TITLES.get(section_id, "模型与计算"), "model", calculation_items, block_metadata_entries(calculation_entries)) or changed
    changed = append_block(section, RISK_BLOCK_TITLES.get(section_id, "风险链条"), "risk_chain", risk_items, block_metadata_entries(risk_entries)) or changed
    changed = append_block(section, TRACKING_BLOCK_TITLES.get(section_id, "跟踪信号"), "tracking", tracking_items, block_metadata_entries(tracking_entries)) or changed
    changed = append_block(section, "外部搜索补证与可比性", "evidence", external_source_items, block_metadata_entries(external_source_entries)) or changed

    if metadata_requires_review([*claim_entries, *fact_entries, *calculation_entries, *risk_entries, *tracking_entries]):
        if section.get("review_required") is not True:
            section["review_required"] = True
            changed = True

    if claims:
        judgements = section.get("judgements")
        if isinstance(judgements, list):
            for claim in claims[:2]:
                if claim not in judgements:
                    judgements.append(claim)
                    changed = True

    changed = merge_legacy_array(section, "facts", fact_items, 4) or changed
    changed = merge_legacy_array(section, "calculations", calculation_items, 4) or changed
    changed = merge_legacy_array(section, "risks_or_improvement_conditions", risk_items + tracking_items, 6) or changed

    if missing:
        missing_fields = section.get("missing_fields")
        if not isinstance(missing_fields, list):
            missing_fields = []
            section["missing_fields"] = missing_fields
        for marker in dedupe([f"research_pack:{item}" for item in missing], 5):
            if marker not in missing_fields:
                missing_fields.append(marker)
                changed = True
        if section.get("review_required") is not True:
            section["review_required"] = True
            changed = True

    refs = section.get("research_pack_refs")
    wanted_refs = collect_agents_for_section(section_id, findings_by_section, item_groups, missing_by_section)
    if wanted_refs and refs != wanted_refs:
        section["research_pack_refs"] = wanted_refs
        changed = True

    return changed


def build_merge_metrics(
    item_groups: dict[str, dict[str, list[dict[str, str]]]],
    findings_by_section: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    by_type = {
        "findings": sum(len(items) for items in findings_by_section.values()),
    }
    for name, group in item_groups.items():
        by_type[name] = sum(len(items) for items in group.values())
    by_section: dict[str, dict[str, int]] = {}
    for section_id in sorted(KNOWN_SECTION_IDS):
        section_metrics = {
            "findings": len(findings_by_section.get(section_id, [])),
        }
        for name, group in item_groups.items():
            section_metrics[name] = len(group.get(section_id, []))
        if any(section_metrics.values()):
            by_section[section_id] = section_metrics
    return {"by_type": by_type, "by_section": by_section}


def build_manifest(
    work_dir: Path,
    packs: list[dict[str, Any]],
    changed_sections: list[str],
    item_groups: dict[str, dict[str, list[dict[str, str]]]],
    findings_by_section: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_by": "merge_research_packs.py",
        "generated_at": now_iso(),
        "work_dir": str(work_dir),
        "pack_count": len(packs),
        "agent_ids": [str(pack.get("agent_id") or "") for pack in packs],
        "changed_sections": changed_sections,
        "review_required_agent_ids": [str(pack.get("agent_id")) for pack in packs if pack.get("review_required")],
        "missing_input_count": sum(len(pack.get("missing_inputs", []) or []) for pack in packs),
        "merge_metrics": build_merge_metrics(item_groups, findings_by_section),
    }


def merge_research_packs(work_dir: Path, section_drafts_path: Path, output: Path | None = None) -> dict[str, Any]:
    output_path = output or section_drafts_path
    drafts = load_json(section_drafts_path)
    research_packs_dir = work_dir / "research_packs"
    packs = load_packs(research_packs_dir)
    findings_by_section = collect_findings_by_section(packs)
    item_groups = collect_pack_items_by_section(packs)
    missing_by_section = collect_missing_by_section(packs)

    sections = drafts.get("sections")
    if not isinstance(sections, list):
        raise ValueError("section_drafts.sections_not_list")

    changed_sections: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        if merge_section(section, findings_by_section, item_groups, missing_by_section):
            changed_sections.append(str(section.get("section_id") or "unknown"))

    manifest = build_manifest(work_dir, packs, changed_sections, item_groups, findings_by_section)
    drafts["research_pack_manifest"] = manifest
    quality_report = drafts.get("quality_report")
    if isinstance(quality_report, dict):
        quality_report["research_pack_manifest"] = manifest
        review_queue = quality_report.get("review_queue")
        if isinstance(review_queue, list):
            for agent_id in manifest["review_required_agent_ids"]:
                marker = f"research_pack_review_required:{agent_id}"
                if marker not in review_queue:
                    review_queue.append(marker)
    dump_json(output_path, drafts)
    return {
        "ok": True,
        "stage": "completed",
        "work_dir": str(work_dir),
        "research_packs_dir": str(research_packs_dir),
        "section_drafts": str(output_path),
        "manifest": manifest,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Merge research_packs into section_drafts.json.")
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--year", type=int, default=2025, help="Reserved for future versioned merge policies.")
    parser.add_argument("--section-drafts", type=Path, help="Default: <work-dir>/section_drafts.json")
    parser.add_argument("--output", type=Path, help="Default: overwrite section drafts in place")
    parser.add_argument("--write-manifest", type=Path, help="Optional separate merge manifest JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    section_drafts_path = args.section_drafts or args.work_dir / "section_drafts.json"
    try:
        result = merge_research_packs(args.work_dir, section_drafts_path, args.output)
    except Exception as exc:
        result = {
            "ok": False,
            "stage": "merge_failed",
            "work_dir": str(args.work_dir),
            "section_drafts": str(section_drafts_path),
            "error": str(exc),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    if args.write_manifest:
        dump_json(args.write_manifest, result["manifest"])
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
