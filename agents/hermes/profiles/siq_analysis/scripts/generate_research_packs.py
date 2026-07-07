#!/usr/bin/env python3
"""Build deterministic SIQ research packs from existing report checkpoints."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


RESEARCH_AGENT_IDS = [
    "evidence_curator",
    "financial_modeler",
    "business_strategy_researcher",
    "industry_peer_researcher",
    "governance_risk_researcher",
]

SECTION_IDS = [
    "executive_summary",
    "key_changes",
    "operating_quality",
    "profitability_and_cost",
    "asset_quality_working_capital",
    "debt_liquidity",
    "cash_flow_quality",
    "industry_competition",
    "strategy_policy_external_risk",
    "governance_compliance_shareholders",
    "valuation_expectation_gap",
    "risk_chain_scenario",
    "tracking_checklist",
    "data_quality_traceability",
]

AGENT_SECTION_IDS = {
    "evidence_curator": SECTION_IDS,
    "financial_modeler": [
        "executive_summary",
        "key_changes",
        "operating_quality",
        "profitability_and_cost",
        "asset_quality_working_capital",
        "debt_liquidity",
        "cash_flow_quality",
        "valuation_expectation_gap",
        "risk_chain_scenario",
        "tracking_checklist",
    ],
    "business_strategy_researcher": [
        "operating_quality",
        "profitability_and_cost",
        "asset_quality_working_capital",
        "cash_flow_quality",
        "industry_competition",
        "strategy_policy_external_risk",
        "risk_chain_scenario",
        "tracking_checklist",
    ],
    "industry_peer_researcher": [
        "industry_competition",
        "strategy_policy_external_risk",
        "valuation_expectation_gap",
        "risk_chain_scenario",
    ],
    "governance_risk_researcher": [
        "debt_liquidity",
        "governance_compliance_shareholders",
        "risk_chain_scenario",
        "tracking_checklist",
        "data_quality_traceability",
    ],
}

CHECKPOINT_PURPOSES = {
    "preflight.json": "company and artifact readiness",
    "wiki_inventory.json": "local wiki file coverage",
    "metric_snapshot.json": "three-year financial metrics",
    "evidence_package.json": "traceable financial evidence",
    "analysis_outline.json": "core thesis, red flags, and derived metrics",
    "peer_metrics.json": "local peer comparison",
    "qualitative_snapshot.json": "semantic annual-report evidence",
    "market_snapshot.json": "price, market cap, and valuation anchors",
    "industry_research.json": "Hermes web-search industry evidence",
}

CORE_METRIC_KEYS = [
    "operating_revenue",
    "parent_net_profit",
    "deducted_parent_net_profit",
    "operating_cash_flow_net",
    "total_assets",
    "total_liabilities",
    "equity_attributable_parent",
    "monetary_capital",
    "inventory",
    "accounts_receivable",
    "research_expenses",
    "development_expenditure",
    "intangible_assets",
    "cash_for_purchases_investments",
]

TECH_R_AND_D_METRIC_KEYS = [
    "research_expenses",
    "development_expenditure",
    "intangible_assets",
    "cash_for_purchases_investments",
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json_if_exists(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"_load_error": f"json_parse_failed:{exc.msg}:line_{exc.lineno}"}
    if isinstance(data, dict):
        return data
    return {"_load_error": "json_root_not_object"}


def dump_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def checkpoint_inputs(work_dir: Path, names: list[str]) -> list[dict[str, str]]:
    inputs: list[dict[str, str]] = []
    for name in names:
        path = work_dir / name
        status = "read" if path.exists() else "missing"
        notes = ""
        if path.exists():
            loaded = load_json_if_exists(path)
            notes = str(loaded.get("_load_error") or "")
            if notes:
                status = "parse_failed"
        inputs.append({
            "path": name,
            "status": status,
            "purpose": CHECKPOINT_PURPOSES.get(name, "checkpoint"),
            "notes": notes,
        })
    return inputs


def read_checkpoints(work_dir: Path) -> dict[str, dict[str, Any]]:
    return {
        name: load_json_if_exists(work_dir / name)
        for name in CHECKPOINT_PURPOSES
    }


def company_id_of(checkpoints: dict[str, dict[str, Any]]) -> str:
    for name in ["preflight.json", "metric_snapshot.json", "industry_research.json", "qualitative_snapshot.json"]:
        value = checkpoints.get(name, {}).get("company_id")
        if value:
            return str(value)
    return "unknown_company"


def metric_entry(snapshot: dict[str, Any], key: str) -> dict[str, Any]:
    metrics = snapshot.get("metrics")
    if not isinstance(metrics, dict):
        return {}
    value = metrics.get(key)
    return value if isinstance(value, dict) else {}


def metric_value(snapshot: dict[str, Any], key: str, year: int) -> Any:
    entry = metric_entry(snapshot, key)
    values = entry.get("values")
    if not isinstance(values, dict):
        return None
    return values.get(str(year))


def metric_ref(snapshot: dict[str, Any], key: str, year: int) -> dict[str, Any]:
    entry = metric_entry(snapshot, key)
    sources = entry.get("sources") if isinstance(entry.get("sources"), dict) else {}
    source = sources.get(str(year)) if isinstance(sources, dict) else {}
    if not isinstance(source, dict):
        source = {}
    ref: dict[str, Any] = {
        "evidence_id": f"metric:{key}:{year}",
        "source_file": str(source.get("file") or "metric_snapshot.json"),
    }
    for target, source_key in [
        ("pdf_page", "pdf_page"),
        ("table_index", "table_index"),
        ("md_line", "md_line"),
    ]:
        if source.get(source_key) is not None:
            ref[target] = source.get(source_key)
    return ref


def fmt_number(value: Any, unit: str | None = None) -> str:
    if value is None:
        return "未取得"
    if isinstance(value, (int, float)):
        if unit:
            return f"{value:.2f}{unit}"
        return f"{value:.2f}"
    return str(value)


def yoy_text(current: Any, previous: Any) -> str:
    if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)) or previous == 0:
        return ""
    return f"，同比 {(current - previous) / abs(previous) * 100:.2f}%"


def metric_fact(snapshot: dict[str, Any], key: str, year: int) -> dict[str, Any] | None:
    entry = metric_entry(snapshot, key)
    if not entry:
        return None
    unit = str(entry.get("unit") or "")
    display = str(entry.get("display_name") or entry.get("canonical_name") or key)
    current = metric_value(snapshot, key, year)
    previous = metric_value(snapshot, key, year - 1)
    fact = f"{display} {year} 年为 {fmt_number(current, unit)}{yoy_text(current, previous)}。"
    return {
        "fact": fact,
        "period": str(year),
        "unit": unit,
        "scope": "financial_metric",
        "evidence_refs": [metric_ref(snapshot, key, year)],
    }


def metric_facts(snapshot: dict[str, Any], keys: list[str], year: int) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for key in keys:
        fact = metric_fact(snapshot, key, year)
        if fact:
            facts.append(fact)
    return facts


def pct_value(snapshot: dict[str, Any], key: str, year: int) -> Any:
    return metric_value(snapshot, key, year)


def ratio_value(numerator: Any, denominator: Any) -> float | None:
    if isinstance(numerator, (int, float)) and isinstance(denominator, (int, float)) and denominator:
        return numerator / abs(denominator)
    return None


def yoy_value(snapshot: dict[str, Any], key: str, year: int) -> float | None:
    current = metric_value(snapshot, key, year)
    previous = metric_value(snapshot, key, year - 1)
    if isinstance(current, (int, float)) and isinstance(previous, (int, float)) and previous:
        return (current - previous) / abs(previous) * 100
    return None


def add_if_text(target: list[dict[str, Any]], item: dict[str, Any] | None) -> None:
    if item:
        target.append(item)


def financial_diagnostic_findings(snapshot: dict[str, Any], outline: dict[str, Any], year: int) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    revenue_yoy = yoy_value(snapshot, "operating_revenue", year)
    profit_yoy = yoy_value(snapshot, "parent_net_profit", year)
    deducted_profit = metric_value(snapshot, "deducted_parent_net_profit", year)
    parent_profit = metric_value(snapshot, "parent_net_profit", year)
    ocf = metric_value(snapshot, "operating_cash_flow_net", year)
    revenue = metric_value(snapshot, "operating_revenue", year)
    debt_ratio = metric_value(snapshot, "debt_to_asset_ratio", year)
    gross_margin = metric_value(snapshot, "gross_margin", year)
    free_cash_flow = metric_value(snapshot, "free_cash_flow", year)

    if isinstance(revenue_yoy, (int, float)) or isinstance(profit_yoy, (int, float)):
        findings.append(key_finding(
            ["key_changes", "operating_quality"],
            (
                f"收入同比 {fmt_number(revenue_yoy, '%')}、归母净利润同比 {fmt_number(profit_yoy, '%')}；"
                "两者方向和幅度决定本期是规模修复、利润率修复，还是低基数利润修复。"
            ),
            0.78,
            "metric_snapshot.json",
            "收入与利润变化需要拆开解释，避免只看净利润弹性。",
        ))
    if isinstance(parent_profit, (int, float)) and isinstance(deducted_profit, (int, float)):
        gap = parent_profit - deducted_profit
        findings.append(key_finding(
            ["profitability_and_cost", "executive_summary"],
            f"归母净利润与扣非归母净利润差额为 {fmt_number(gap, '亿元')}，需要区分主营盈利修复和非经常性因素贡献。",
            0.78,
            "metric_snapshot.json",
            "盈利质量判断必须同时看扣非口径。",
        ))
    ocf_profit_ratio = ratio_value(ocf, parent_profit)
    if ocf_profit_ratio is not None:
        findings.append(key_finding(
            ["cash_flow_quality", "executive_summary"],
            f"经营现金流/归母净利润约 {fmt_number(ocf_profit_ratio, 'x')}，用于判断利润是否转化为现金回款。",
            0.8,
            "metric_snapshot.json",
            "现金流覆盖利润是质量判断的核心闸门。",
        ))
    ocf_revenue_ratio = ratio_value(ocf, revenue)
    if ocf_revenue_ratio is not None:
        findings.append(key_finding(
            ["cash_flow_quality", "operating_quality"],
            f"经营现金流/营业收入约 {fmt_number(ocf_revenue_ratio * 100, '%')}，可与毛利率和应收存货变化交叉验证收入质量。",
            0.76,
            "metric_snapshot.json",
            "经营现金流占收入比例用于识别规模增长是否消耗现金。",
        ))
    if isinstance(gross_margin, (int, float)):
        findings.append(key_finding(
            ["profitability_and_cost", "industry_competition"],
            f"毛利率为 {fmt_number(gross_margin, '%')}，需要与同业分位、价格竞争和产品结构一起解释。",
            0.76,
            "metric_snapshot.json",
            "单看收入增长无法说明盈利弹性。",
        ))
    if isinstance(debt_ratio, (int, float)):
        findings.append(key_finding(
            ["debt_liquidity", "risk_chain_scenario"],
            f"资产负债率为 {fmt_number(debt_ratio, '%')}，偿债章节应同时检查货币资金、短债和经营现金流覆盖。",
            0.76,
            "metric_snapshot.json",
            "杠杆水平需要与现金流和短债压力联动判断。",
        ))
    if isinstance(free_cash_flow, (int, float)):
        direction = "为负" if free_cash_flow < 0 else "为正"
        findings.append(key_finding(
            ["cash_flow_quality", "risk_chain_scenario"],
            f"自由现金流 {direction}（{fmt_number(free_cash_flow, '亿元')}），会影响资本开支、转型投入和债务滚续弹性。",
            0.76,
            "analysis_outline.json",
            "自由现金流是利润质量和资本开支压力的交叉指标。",
        ))
    return findings


def financial_model_calculations(snapshot: dict[str, Any], outline: dict[str, Any], year: int) -> list[dict[str, Any]]:
    calculations: list[dict[str, Any]] = []
    revenue = metric_value(snapshot, "operating_revenue", year)
    parent_profit = metric_value(snapshot, "parent_net_profit", year)
    deducted_profit = metric_value(snapshot, "deducted_parent_net_profit", year)
    ocf = metric_value(snapshot, "operating_cash_flow_net", year)
    assets = metric_value(snapshot, "total_assets", year)
    equity = metric_value(snapshot, "equity_attributable_parent", year)
    liabilities = metric_value(snapshot, "total_liabilities", year)
    inventory = metric_value(snapshot, "inventory", year)
    receivables = metric_value(snapshot, "accounts_receivable", year)

    if isinstance(parent_profit, (int, float)) and isinstance(revenue, (int, float)) and revenue:
        calculations.append(calculation(
            "net_margin_pct",
            "归母净利润/营业收入",
            {"parent_net_profit": parent_profit, "operating_revenue": revenue, "period": str(year)},
            parent_profit / revenue * 100,
            "metric_snapshot.json",
        ))
    if isinstance(deducted_profit, (int, float)) and isinstance(revenue, (int, float)) and revenue:
        calculations.append(calculation(
            "deducted_net_margin_pct",
            "扣非归母净利润/营业收入",
            {"deducted_parent_net_profit": deducted_profit, "operating_revenue": revenue, "period": str(year)},
            deducted_profit / revenue * 100,
            "metric_snapshot.json",
        ))
    if isinstance(ocf, (int, float)) and isinstance(parent_profit, (int, float)) and parent_profit:
        calculations.append(calculation(
            "ocf_to_parent_profit",
            "经营现金流净额/归母净利润",
            {"operating_cash_flow_net": ocf, "parent_net_profit": parent_profit, "period": str(year)},
            ocf / abs(parent_profit),
            "metric_snapshot.json",
        ))
    if isinstance(ocf, (int, float)) and isinstance(revenue, (int, float)) and revenue:
        calculations.append(calculation(
            "ocf_to_revenue_pct",
            "经营现金流净额/营业收入",
            {"operating_cash_flow_net": ocf, "operating_revenue": revenue, "period": str(year)},
            ocf / revenue * 100,
            "metric_snapshot.json",
        ))
    if isinstance(parent_profit, (int, float)) and isinstance(assets, (int, float)) and assets:
        calculations.append(calculation(
            "roa_parent_pct",
            "归母净利润/总资产",
            {"parent_net_profit": parent_profit, "total_assets": assets, "period": str(year)},
            parent_profit / assets * 100,
            "metric_snapshot.json",
        ))
    if isinstance(parent_profit, (int, float)) and isinstance(equity, (int, float)) and equity:
        calculations.append(calculation(
            "roe_parent_pct",
            "归母净利润/归母净资产",
            {"parent_net_profit": parent_profit, "equity_attributable_parent": equity, "period": str(year)},
            parent_profit / equity * 100,
            "metric_snapshot.json",
        ))
    if isinstance(liabilities, (int, float)) and isinstance(assets, (int, float)) and assets:
        calculations.append(calculation(
            "debt_to_asset_ratio_pct",
            "总负债/总资产",
            {"total_liabilities": liabilities, "total_assets": assets, "period": str(year)},
            liabilities / assets * 100,
            "metric_snapshot.json",
        ))
    if isinstance(inventory, (int, float)) and isinstance(revenue, (int, float)) and revenue:
        calculations.append(calculation(
            "inventory_to_revenue_pct",
            "存货/营业收入",
            {"inventory": inventory, "operating_revenue": revenue, "period": str(year)},
            inventory / revenue * 100,
            "metric_snapshot.json",
        ))
    if isinstance(receivables, (int, float)) and isinstance(revenue, (int, float)) and revenue:
        calculations.append(calculation(
            "accounts_receivable_to_revenue_pct",
            "应收账款/营业收入",
            {"accounts_receivable": receivables, "operating_revenue": revenue, "period": str(year)},
            receivables / revenue * 100,
            "metric_snapshot.json",
        ))
    return calculations


def technology_rd_findings(snapshot: dict[str, Any], qualitative: dict[str, Any], year: int) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    revenue = metric_value(snapshot, "operating_revenue", year)
    research_expenses = metric_value(snapshot, "research_expenses", year)
    development_expenditure = metric_value(snapshot, "development_expenditure", year)
    intangible_assets = metric_value(snapshot, "intangible_assets", year)
    total_assets = metric_value(snapshot, "total_assets", year)
    capex_cash = metric_value(snapshot, "cash_for_purchases_investments", year)
    ocf = metric_value(snapshot, "operating_cash_flow_net", year)
    gross_margin = metric_value(snapshot, "gross_margin", year)
    rd_items = qualitative_items(qualitative, ["rd_technology"], 4)

    if isinstance(research_expenses, (int, float)) and isinstance(revenue, (int, float)) and revenue:
        rd_ratio = research_expenses / revenue * 100
        findings.append(key_finding(
            ["profitability_and_cost", "strategy_policy_external_risk", "tracking_checklist"],
            (
                f"研发费用为 {fmt_number(research_expenses, '亿元')}，研发费用率约 {fmt_number(rd_ratio, '%')}；"
                "科技/制造业分析必须继续验证研发投入是否传导到产品结构、毛利率、扣非利润和现金流。"
            ),
            0.78,
            "metric_snapshot.json",
            "rd_expense_intensity",
        ))
    if isinstance(development_expenditure, (int, float)) and isinstance(total_assets, (int, float)) and total_assets:
        findings.append(key_finding(
            ["asset_quality_working_capital", "cash_flow_quality", "risk_chain_scenario"],
            (
                f"开发支出为 {fmt_number(development_expenditure, '亿元')}，占总资产约 "
                f"{fmt_number(development_expenditure / total_assets * 100, '%')}；需观察后续转入无形资产后的摊销或减值压力。"
            ),
            0.74,
            "metric_snapshot.json",
            "development_expenditure_asset_quality",
        ))
    if isinstance(intangible_assets, (int, float)) and isinstance(total_assets, (int, float)) and total_assets:
        findings.append(key_finding(
            ["asset_quality_working_capital", "strategy_policy_external_risk"],
            (
                f"无形资产为 {fmt_number(intangible_assets, '亿元')}，占总资产约 "
                f"{fmt_number(intangible_assets / total_assets * 100, '%')}；技术资产质量需要结合专利/非专利技术、软件、摊销和减值证据复核。"
            ),
            0.72,
            "metric_snapshot.json",
            "intangible_asset_quality",
        ))
    if isinstance(capex_cash, (int, float)) and isinstance(ocf, (int, float)) and ocf:
        findings.append(key_finding(
            ["cash_flow_quality", "debt_liquidity", "strategy_policy_external_risk"],
            (
                f"购建固定资产、无形资产和其他长期资产支付的现金/经营现金流约 "
                f"{fmt_number(capex_cash / abs(ocf), 'x')}；制造业转型投入需要用 FCF 和债务覆盖验证。"
            ),
            0.74,
            "metric_snapshot.json",
            "capex_cash_burden",
        ))
    if rd_items:
        findings.append(key_finding(
            ["strategy_policy_external_risk", "industry_competition", "tracking_checklist"],
            "年报存在研发/技术语义证据；相关表述不能单独证明技术壁垒，必须与毛利率、产品结构、客户验证、专利质量或同业分位交叉验证。",
            0.7,
            "qualitative_snapshot.json",
            "rd_technology_evidence_requires_financial_validation",
        ))
    if isinstance(gross_margin, (int, float)) and isinstance(research_expenses, (int, float)):
        findings.append(key_finding(
            ["profitability_and_cost", "industry_competition", "risk_chain_scenario"],
            (
                f"毛利率为 {fmt_number(gross_margin, '%')}，研发费用为 {fmt_number(research_expenses, '亿元')}；"
                "若研发强度上升但毛利率和扣非利润未改善，需要降级技术投入产出判断。"
            ),
            0.72,
            "metric_snapshot.json",
            "rd_to_margin_validation",
        ))
    return findings


def technology_rd_calculations(snapshot: dict[str, Any], year: int) -> list[dict[str, Any]]:
    calculations: list[dict[str, Any]] = []
    revenue = metric_value(snapshot, "operating_revenue", year)
    research_expenses = metric_value(snapshot, "research_expenses", year)
    deducted_profit = metric_value(snapshot, "deducted_parent_net_profit", year)
    development_expenditure = metric_value(snapshot, "development_expenditure", year)
    intangible_assets = metric_value(snapshot, "intangible_assets", year)
    total_assets = metric_value(snapshot, "total_assets", year)
    capex_cash = metric_value(snapshot, "cash_for_purchases_investments", year)
    ocf = metric_value(snapshot, "operating_cash_flow_net", year)
    if isinstance(research_expenses, (int, float)) and isinstance(revenue, (int, float)) and revenue:
        calculations.append(calculation(
            "rd_expense_ratio_pct",
            "研发费用/营业收入",
            {"research_expenses": research_expenses, "operating_revenue": revenue, "period": str(year)},
            {
                "value_pct": research_expenses / revenue * 100,
                "period": str(year),
                "reliability": "reported_expensed_rd_only",
                "interpretation": "反映费用化研发强度；不等同于研发投入合计或研发资本化比例。",
            },
            "metric_snapshot.json",
        ))
    rd_yoy = yoy_value(snapshot, "research_expenses", year)
    if isinstance(rd_yoy, (int, float)):
        calculations.append(calculation(
            "rd_expense_yoy_pct",
            "(本期研发费用-上期研发费用)/上期研发费用",
            {"period": str(year), "metric": "research_expenses"},
            {"value_pct": rd_yoy, "reliability": "reported_expensed_rd"},
            "metric_snapshot.json",
        ))
    if isinstance(research_expenses, (int, float)) and isinstance(deducted_profit, (int, float)) and deducted_profit:
        calculations.append(calculation(
            "rd_expense_to_deducted_profit",
            "研发费用/扣非归母净利润",
            {"research_expenses": research_expenses, "deducted_parent_net_profit": deducted_profit, "period": str(year)},
            {
                "value_x": research_expenses / abs(deducted_profit),
                "interpretation": "用于观察研发费用刚性对主营盈利弹性的压力。",
            },
            "metric_snapshot.json",
        ))
    if isinstance(development_expenditure, (int, float)) and isinstance(total_assets, (int, float)) and total_assets:
        calculations.append(calculation(
            "development_expenditure_to_assets_pct",
            "开发支出/总资产",
            {"development_expenditure": development_expenditure, "total_assets": total_assets, "period": str(year)},
            {
                "value_pct": development_expenditure / total_assets * 100,
                "interpretation": "开发支出累积会影响后续无形资产、摊销和减值风险。",
            },
            "metric_snapshot.json",
        ))
    if isinstance(intangible_assets, (int, float)) and isinstance(total_assets, (int, float)) and total_assets:
        calculations.append(calculation(
            "intangible_assets_to_assets_pct",
            "无形资产/总资产",
            {"intangible_assets": intangible_assets, "total_assets": total_assets, "period": str(year)},
            {
                "value_pct": intangible_assets / total_assets * 100,
                "interpretation": "用于评估技术、软件、专利和非专利技术等资产对资产质量的影响。",
            },
            "metric_snapshot.json",
        ))
    if isinstance(capex_cash, (int, float)) and isinstance(ocf, (int, float)) and ocf:
        calculations.append(calculation(
            "capex_cash_to_ocf",
            "购建固定资产、无形资产和其他长期资产支付的现金/经营现金流净额",
            {"cash_for_purchases_investments": capex_cash, "operating_cash_flow_net": ocf, "period": str(year)},
            {
                "value_x": capex_cash / abs(ocf),
                "interpretation": "制造业扩产、技改和技术投入对自由现金流的压力指标。",
            },
            "metric_snapshot.json",
        ))
    return calculations


def technology_rd_risk_chains(snapshot: dict[str, Any], qualitative: dict[str, Any], year: int) -> list[dict[str, Any]]:
    chains: list[dict[str, Any]] = []
    research_expenses = metric_value(snapshot, "research_expenses", year)
    revenue = metric_value(snapshot, "operating_revenue", year)
    gross_margin = metric_value(snapshot, "gross_margin", year)
    development_expenditure = metric_value(snapshot, "development_expenditure", year)
    intangible_assets = metric_value(snapshot, "intangible_assets", year)
    capex_cash = metric_value(snapshot, "cash_for_purchases_investments", year)
    ocf = metric_value(snapshot, "operating_cash_flow_net", year)
    rd_items = qualitative_items(qualitative, ["rd_technology"], 4)
    rd_ratio = research_expenses / revenue * 100 if isinstance(research_expenses, (int, float)) and isinstance(revenue, (int, float)) and revenue else None

    if rd_ratio is not None:
        chains.append(risk_chain(
            [
                f"研发费用率约 {fmt_number(rd_ratio, '%')}",
                "技术投入需要转化为产品结构、价格权或成本效率",
                f"当前毛利率 {fmt_number(gross_margin, '%')}，若未改善则技术投入产出判断降级",
            ],
            ["profitability_and_cost", "strategy_policy_external_risk", "risk_chain_scenario"],
            "medium",
        ))
    if isinstance(development_expenditure, (int, float)) or isinstance(intangible_assets, (int, float)):
        chains.append(risk_chain(
            [
                f"开发支出 {fmt_number(development_expenditure, '亿元')}、无形资产 {fmt_number(intangible_assets, '亿元')}",
                "资本化或技术资产累积会降低当期费用压力但增加未来摊销/减值观察",
                "若产品销量、毛利率或客户认证不及预期，资产质量风险上升",
            ],
            ["asset_quality_working_capital", "profitability_and_cost", "risk_chain_scenario"],
            "medium",
        ))
    if isinstance(capex_cash, (int, float)) and isinstance(ocf, (int, float)) and ocf and capex_cash > abs(ocf) * 0.5:
        chains.append(risk_chain(
            [
                f"长期资产购建现金支出 {fmt_number(capex_cash, '亿元')}",
                f"经营现金流 {fmt_number(ocf, '亿元')}",
                "制造业技改、扩产和研发现金投入会压缩自由现金流",
                "需跟踪融资、短债覆盖和资本开支削减弹性",
            ],
            ["cash_flow_quality", "debt_liquidity", "risk_chain_scenario"],
            "medium",
        ))
    if rd_items:
        chains.append(risk_chain(
            [
                "年报披露研发/技术方向",
                "需要外部专利、客户验证、产品代际或同业技术路线补证",
                "若只停留在研发叙事，不能上升为技术壁垒结论",
            ],
            ["strategy_policy_external_risk", "industry_competition", "tracking_checklist"],
            "medium",
        ))
    return chains


def technology_rd_tracking_signals(snapshot: dict[str, Any], qualitative: dict[str, Any], year: int) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for key, label, sections in [
        ("research_expenses", "研发费用及研发费用率", ["profitability_and_cost", "tracking_checklist"]),
        ("development_expenditure", "开发支出余额和转无形资产节奏", ["asset_quality_working_capital", "tracking_checklist"]),
        ("intangible_assets", "无形资产摊销和减值风险", ["asset_quality_working_capital", "tracking_checklist"]),
        ("cash_for_purchases_investments", "长期资产购建现金支出", ["cash_flow_quality", "tracking_checklist"]),
    ]:
        value = metric_value(snapshot, key, year)
        if value is not None:
            unit = metric_entry(snapshot, key).get("unit")
            signals.append(tracking_signal(
                f"{label}后续变化，当前值为 {fmt_number(value, unit)}",
                sections,
                "confirm",
                "metric_snapshot.json",
            ))
    if qualitative_items(qualitative, ["rd_technology"], 1):
        signals.extend([
            tracking_signal("研发投入是否带来毛利率、产品结构、客户认证或同业分位改善", ["tracking_checklist", "strategy_policy_external_risk"], "confirm", "qualitative_snapshot.json"),
            tracking_signal("专利/非专利技术、软件和核心技术披露是否能与收入和现金流改善交叉验证", ["tracking_checklist", "industry_competition"], "confirm", "Hermes Tavily/EXA or annual report notes"),
        ])
    return signals[:7]


def key_finding(section_ids: list[str], claim: str, confidence: float, source_file: str, rationale: str = "") -> dict[str, Any]:
    return {
        "section_ids": section_ids,
        "claim": claim,
        "confidence": confidence,
        "rationale": rationale,
        "evidence_refs": [{"source_file": source_file}],
    }


def calculation(name: str, formula: str, inputs: Any, output: Any, source_file: str) -> dict[str, Any]:
    return {
        "name": name,
        "formula": formula,
        "inputs": inputs,
        "output": output,
        "evidence_refs": [{"source_file": source_file}],
    }


def missing_input(name: str, reason: str, impact: str, section_ids: list[str], requested_action: str = "") -> dict[str, Any]:
    item = {
        "name": name,
        "reason": reason,
        "impact": impact,
        "section_ids": section_ids,
    }
    if requested_action:
        item["requested_action"] = requested_action
    return item


def risk_chain(items: list[str], section_ids: list[str], severity: str = "unknown") -> dict[str, Any]:
    chain = [str(item).strip() for item in items if str(item).strip()]
    if len(chain) < 2:
        chain = [chain[0], "需要补充财务或外部证据验证"] if chain else ["证据不足", "结论需降级表达"]
    return {
        "section_ids": section_ids,
        "chain": chain[:5],
        "severity": severity,
        "evidence_refs": [{"source_file": "analysis_outline.json"}],
        "counter_signals": [],
    }


def tracking_signal(text: str, section_ids: list[str], direction: str = "unknown", source_hint: str = "annual report") -> dict[str, Any]:
    return {
        "signal": text,
        "why_it_matters": "用于确认或推翻当前分析结论。",
        "direction": direction,
        "source_hint": source_hint,
        "section_ids": section_ids,
    }


def qualitative_items(qualitative: dict[str, Any], buckets: list[str], limit: int = 8) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    bucket_map = qualitative.get("buckets")
    if not isinstance(bucket_map, dict):
        return result
    for bucket in buckets:
        values = bucket_map.get(bucket)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict) and str(item.get("text") or "").strip():
                result.append(item)
            if len(result) >= limit:
                return result
    return result


NOISY_STRATEGY_TERMS = [
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
    "主营业务分行业、分产品、分地区、分销售模式情况",
    "单位：元 币种",
    "公司报告期内业务、产品或服务发生重大变化或调整有关情况",
    "产品质量保证金",
    "分部信息",
    "收入确认方式及计量方法",
]


def is_noisy_strategy_item(item: dict[str, Any]) -> bool:
    text = str(item.get("text") or "")
    return any(term in text for term in NOISY_STRATEGY_TERMS)


def qualitative_items_filtered(qualitative: dict[str, Any], buckets: list[str], limit: int = 8) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in qualitative_items(qualitative, buckets, limit * 3):
        if is_noisy_strategy_item(item):
            continue
        text = compact_text(item.get("text"), 220)
        key = "".join(text.split())
        if not key or key in seen:
            continue
        result.append(item)
        seen.add(key)
        if len(result) >= limit:
            break
    return result


def compact_text(value: Any, limit: int = 160) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def strategy_evidence_fact_text(item: dict[str, Any]) -> str:
    text = re.sub(r"（证据：[^）]+）", "", str(item.get("text") or ""))
    return compact_text(text, 220)


def strategy_finding_from_item(item: dict[str, Any], snapshot: dict[str, Any], year: int) -> dict[str, Any]:
    text = str(item.get("text") or "").strip()
    theme, variables, signals = strategy_theme(text)
    revenue = metric_value(snapshot, "operating_revenue", year)
    gross_margin = metric_value(snapshot, "gross_margin", year)
    ocf = metric_value(snapshot, "operating_cash_flow_net", year)
    rd_expenses = metric_value(snapshot, "research_expenses", year)
    fact = compact_text(text, 120)
    claim = (
        f"{theme}：本地年报证据显示「{fact}」。"
        f"该判断需用 {'、'.join(variables[:4])} 验证；当前可见财务锚包括"
        f"收入 {fmt_number(revenue, '亿元')}、毛利率 {fmt_number(gross_margin, '%')}、"
        f"经营现金流 {fmt_number(ocf, '亿元')}、研发费用 {fmt_number(rd_expenses, '亿元')}。"
    )
    return key_finding(
        ["operating_quality", "profitability_and_cost", "strategy_policy_external_risk", "tracking_checklist"],
        claim,
        0.72,
        "qualitative_snapshot.json",
        "事实依据 -> 经营含义 -> 财务变量 -> 反证条件:" + "；".join(signals[:2]),
    )


def qualitative_fact(item: dict[str, Any], section_ids: list[str]) -> dict[str, Any]:
    refs = [
        {"evidence_id": str(eid), "source_file": "qualitative_snapshot.json"}
        for eid in item.get("evidence_ids", [])[:4]
        if str(eid).strip()
    ] or [{"source_file": "qualitative_snapshot.json"}]
    return {
        "fact": strategy_evidence_fact_text(item),
        "period": "",
        "unit": "",
        "scope": ",".join(section_ids),
        "evidence_refs": refs,
        "notes": str(item.get("kind") or ""),
    }


def strategy_theme(text: str) -> tuple[str, list[str], list[str]]:
    lowered = text.lower()
    if any(keyword in text for keyword in ["研发", "技术", "核心技术", "智能", "新能源", "电动"]):
        return (
            "技术与产品升级",
            ["研发投入", "毛利率", "资本开支", "产品结构", "新能源销量占比"],
            ["研发投入是否形成毛利率或销量结构改善", "资本开支是否被经营现金流覆盖"],
        )
    if any(keyword in text for keyword in ["出口", "海外", "国际", "区域", "全球"]):
        return (
            "海外与区域扩张",
            ["海外收入", "汇率与地缘风险", "毛利率", "应收账款", "经营现金流"],
            ["海外销量或收入是否增长", "回款和汇率风险是否侵蚀现金流"],
        )
    if any(keyword in text for keyword in ["成本", "费用", "效率", "运营", "改革", "协同"]):
        return (
            "效率与成本改善",
            ["毛利率", "期间费用率", "扣非利润", "经营现金流", "库存周转"],
            ["费用率是否下降", "扣非利润和经营现金流是否同向改善"],
        )
    if any(keyword in text for keyword in ["产能", "生产", "供应", "芯片", "原材料"]):
        return (
            "产能与供应链韧性",
            ["产能利用", "存货", "营业成本", "毛利率", "交付节奏"],
            ["存货是否异常累积", "成本压力是否传导到毛利率"],
        )
    if "policy" in lowered or any(keyword in text for keyword in ["政策", "购置税", "补贴", "两新"]):
        return (
            "政策敏感性",
            ["销量", "毛利率", "费用投放", "收入增速", "库存"],
            ["政策退坡后销量是否承压", "价格促销是否侵蚀毛利率"],
        )
    return (
        "业务结构验证",
        ["营业收入", "毛利率", "扣非利润", "经营现金流"],
        ["收入变化是否同步传导到毛利率、扣非利润和现金流"],
    )


def strategy_bridge_finding(item: dict[str, Any]) -> dict[str, Any]:
    text = str(item.get("text") or "").strip()
    theme, variables, signals = strategy_theme(text)
    claim = (
        f"{theme}是该业务证据的主要分析入口；后续应验证"
        f"{'、'.join(variables[:5])}，不能只停留在战略口号或业务描述。"
    )
    return key_finding(
        ["operating_quality", "profitability_and_cost", "strategy_policy_external_risk", "tracking_checklist"],
        claim,
        0.7,
        "qualitative_snapshot.json",
        "strategy_to_financial_variables:" + "；".join(signals[:2]),
    )


def strategy_tracking_signals(item: dict[str, Any]) -> list[dict[str, Any]]:
    text = str(item.get("text") or "").strip()
    theme, variables, signals = strategy_theme(text)
    result = [
        tracking_signal(
            f"{theme}：跟踪 {'、'.join(variables[:4])}",
            ["tracking_checklist", "strategy_policy_external_risk"],
            "confirm",
            "qualitative_snapshot.json",
        )
    ]
    for signal in signals[:2]:
        result.append(tracking_signal(signal, ["tracking_checklist"], "confirm", "metric_snapshot.json"))
    return result


def strategy_execution_calculations(snapshot: dict[str, Any], year: int) -> list[dict[str, Any]]:
    calculations: list[dict[str, Any]] = []
    revenue = metric_value(snapshot, "operating_revenue", year)
    research_expenses = metric_value(snapshot, "research_expenses", year)
    capex_cash = metric_value(snapshot, "cash_for_purchases_investments", year)
    assets = metric_value(snapshot, "total_assets", year)
    fixed_assets = metric_value(snapshot, "fixed_assets", year)
    inventory = metric_value(snapshot, "inventory", year)
    receivables = metric_value(snapshot, "accounts_receivable", year)
    if isinstance(research_expenses, (int, float)) and isinstance(revenue, (int, float)) and revenue:
        calculations.append(calculation(
            "strategy_rd_intensity_pct",
            "研发费用/营业收入",
            {"research_expenses": research_expenses, "operating_revenue": revenue, "period": str(year)},
            {
                "value_pct": research_expenses / revenue * 100,
                "strategy_read": "研发强度是技术战略投入项，需由产品收入、毛利率、客户验证和现金流交叉验证。",
            },
            "metric_snapshot.json",
        ))
    if isinstance(capex_cash, (int, float)) and isinstance(revenue, (int, float)) and revenue:
        calculations.append(calculation(
            "strategy_capex_cash_to_revenue_pct",
            "购建固定资产、无形资产和其他长期资产支付的现金/营业收入",
            {"cash_for_purchases_investments": capex_cash, "operating_revenue": revenue, "period": str(year)},
            {
                "value_pct": capex_cash / revenue * 100,
                "strategy_read": "制造业扩产、技改和技术路线投入会先表现为现金支出和资产累积。",
            },
            "metric_snapshot.json",
        ))
    if isinstance(fixed_assets, (int, float)) and isinstance(assets, (int, float)) and assets:
        calculations.append(calculation(
            "fixed_assets_to_assets_pct",
            "固定资产/总资产",
            {"fixed_assets": fixed_assets, "total_assets": assets, "period": str(year)},
            {
                "value_pct": fixed_assets / assets * 100,
                "strategy_read": "生产制造资产占比用于观察量产、产能和折旧压力。",
            },
            "metric_snapshot.json",
        ))
    if isinstance(inventory, (int, float)) and isinstance(revenue, (int, float)) and revenue:
        calculations.append(calculation(
            "strategy_inventory_to_revenue_pct",
            "存货/营业收入",
            {"inventory": inventory, "operating_revenue": revenue, "period": str(year)},
            {
                "value_pct": inventory / revenue * 100,
                "strategy_read": "产能扩张或产品迭代若未转化为销售，可能先表现为存货压力。",
            },
            "metric_snapshot.json",
        ))
    if isinstance(receivables, (int, float)) and isinstance(revenue, (int, float)) and revenue:
        calculations.append(calculation(
            "strategy_receivables_to_revenue_pct",
            "应收账款/营业收入",
            {"accounts_receivable": receivables, "operating_revenue": revenue, "period": str(year)},
            {
                "value_pct": receivables / revenue * 100,
                "strategy_read": "渠道、客户和区域扩张需要通过回款质量验证。",
            },
            "metric_snapshot.json",
        ))
    return calculations


def strategy_missing_inputs(qualitative: dict[str, Any]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    required_topics = {
        "product_or_customer_validation": ("product_brand", "缺少产品代际、客户认证、订单或收入结构证据。"),
        "technology_barrier_detail": ("rd_technology", "缺少核心技术、专利质量、研发项目或技术资产与产品的对应关系。"),
        "capacity_mass_production": ("operation_driver", "缺少产能利用、量产爬坡、交付节奏或库存转化证据。"),
    }
    for name, (bucket, reason) in required_topics.items():
        if not qualitative_items(qualitative, [bucket], 1):
            missing.append(missing_input(
                name,
                reason,
                "战略/技术/制造分析只能保守表达，不能证明商业化兑现。",
                ["operating_quality", "strategy_policy_external_risk", "tracking_checklist"],
                "补充年报分部、临时公告、客户认证、专利或产能利用资料。",
            ))
    return missing


PEER_METRIC_LABELS = {
    "operating_revenue_yi": "收入规模",
    "operating_revenue_yoy_pct": "收入增速",
    "gross_margin_pct": "毛利率",
    "parent_net_profit_yi": "归母净利润",
    "deducted_parent_net_profit_yi": "扣非归母净利润",
    "operating_cash_flow_net_yi": "经营现金流",
    "debt_to_asset_ratio_pct": "资产负债率",
}

PEER_METRIC_UNITS = {
    "operating_revenue_yi": "亿元",
    "operating_revenue_yoy_pct": "%",
    "gross_margin_pct": "%",
    "parent_net_profit_yi": "亿元",
    "deducted_parent_net_profit_yi": "亿元",
    "operating_cash_flow_net_yi": "亿元",
    "debt_to_asset_ratio_pct": "%",
}


def peer_percentile_finding(metric_key: str, item: dict[str, Any]) -> dict[str, Any] | None:
    percentile = item.get("target_percentile")
    target = item.get("target_value")
    median = item.get("median")
    sample_count = item.get("sample_count")
    if not isinstance(percentile, (int, float)):
        return None
    label = PEER_METRIC_LABELS.get(metric_key, metric_key)
    if percentile >= 75:
        position = "高于多数同业"
    elif percentile <= 35:
        position = "低于多数同业"
    else:
        position = "处于同业中部区间"
    unit = PEER_METRIC_UNITS.get(metric_key)
    claim = (
        f"{label}目标值 {fmt_number(target, unit)}，同业中位 {fmt_number(median, unit)}，"
        f"约处于 {fmt_number(percentile, '%')} 分位（样本 {sample_count} 家），{position}。"
    )
    sections = ["industry_competition"]
    if any(token in metric_key for token in ["gross_margin", "profit"]):
        sections.append("profitability_and_cost")
    if "cash_flow" in metric_key:
        sections.append("cash_flow_quality")
    if "debt" in metric_key:
        sections.append("debt_liquidity")
    if any(token in metric_key for token in ["profit", "revenue", "gross_margin"]):
        sections.append("valuation_expectation_gap")
    return key_finding(sections, claim, 0.74, "peer_metrics.json", "peer_percentile")


def peer_strength_weaknesses(aggregates: Any) -> tuple[list[str], list[str]]:
    strengths: list[str] = []
    weaknesses: list[str] = []
    if not isinstance(aggregates, dict):
        return strengths, weaknesses
    for metric_key, label in PEER_METRIC_LABELS.items():
        item = aggregates.get(metric_key)
        if not isinstance(item, dict):
            continue
        percentile = item.get("target_percentile")
        if not isinstance(percentile, (int, float)):
            continue
        if percentile >= 75:
            strengths.append(f"{label}处于较高分位")
        elif percentile <= 35:
            weaknesses.append(f"{label}处于较低分位")
    return strengths[:4], weaknesses[:4]


def peer_metric_risk_chains(aggregates: Any) -> list[dict[str, Any]]:
    chains: list[dict[str, Any]] = []
    if not isinstance(aggregates, dict):
        return chains
    revenue = aggregates.get("operating_revenue_yi") if isinstance(aggregates.get("operating_revenue_yi"), dict) else {}
    gross_margin = aggregates.get("gross_margin_pct") if isinstance(aggregates.get("gross_margin_pct"), dict) else {}
    ocf = aggregates.get("operating_cash_flow_net_yi") if isinstance(aggregates.get("operating_cash_flow_net_yi"), dict) else {}
    debt = aggregates.get("debt_to_asset_ratio_pct") if isinstance(aggregates.get("debt_to_asset_ratio_pct"), dict) else {}

    revenue_pct = revenue.get("target_percentile")
    margin_pct = gross_margin.get("target_percentile")
    ocf_pct = ocf.get("target_percentile")
    debt_pct = debt.get("target_percentile")
    if isinstance(revenue_pct, (int, float)) and isinstance(margin_pct, (int, float)) and revenue_pct >= 65 and margin_pct <= 35:
        chains.append(risk_chain(
            [
                "收入规模处于较高分位但毛利率偏低",
                "规模优势尚未有效转化为价格权或成本优势",
                "若价格竞争持续，盈利弹性和估值修复都需要降级",
            ],
            ["industry_competition", "profitability_and_cost", "valuation_expectation_gap", "risk_chain_scenario"],
            "medium",
        ))
    if isinstance(ocf_pct, (int, float)) and ocf_pct <= 35:
        chains.append(risk_chain(
            [
                "经营现金流同业分位偏低",
                "收入或利润修复可能伴随营运资本占用",
                "需用应收、存货、应付和订单回款验证增长质量",
            ],
            ["industry_competition", "cash_flow_quality", "asset_quality_working_capital", "risk_chain_scenario"],
            "medium",
        ))
    if isinstance(debt_pct, (int, float)) and debt_pct >= 70:
        chains.append(risk_chain(
            [
                "资产负债率处于较高同业分位",
                "转型投入、库存和价格竞争会放大流动性敏感性",
                "需要跟踪货币资金、短债覆盖和经营现金流",
            ],
            ["industry_competition", "debt_liquidity", "cash_flow_quality", "risk_chain_scenario"],
            "medium",
        ))
    return chains


def peer_tracking_signals(aggregates: Any) -> list[dict[str, Any]]:
    signals = [
        tracking_signal(
            "同业分位变化：收入规模、毛利率、扣非归母净利润、经营现金流、资产负债率",
            ["industry_competition", "tracking_checklist"],
            "confirm",
            "peer_metrics.json",
        )
    ]
    if isinstance(aggregates, dict):
        for metric_key, label in PEER_METRIC_LABELS.items():
            item = aggregates.get(metric_key)
            if not isinstance(item, dict):
                continue
            percentile = item.get("target_percentile")
            if isinstance(percentile, (int, float)):
                signals.append(tracking_signal(
                    f"{label}同业分位是否从 {fmt_number(percentile, '%')} 继续改善或恶化",
                    ["industry_competition", "tracking_checklist"],
                    "confirm",
                    "peer_metrics.json",
                ))
    return signals[:6]


def external_sources(industry_research: dict[str, Any]) -> list[dict[str, Any]]:
    queries = industry_research.get("queries") if isinstance(industry_research.get("queries"), list) else []
    default_query = str(queries[0]) if queries else "industry research"
    sources: list[dict[str, Any]] = []
    results = industry_research.get("results")
    if not isinstance(results, list):
        return sources
    for index, item in enumerate(results[:12]):
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip()
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        if not provider or not title or not url:
            continue
        sources.append({
            "provider": provider,
            "query": str(item.get("query") or (queries[index % len(queries)] if queries else default_query)),
            "url": url,
            "title": title,
            "retrieved_at": str(industry_research.get("generated_at") or now_iso()),
            "summary": str(item.get("snippet") or "")[:500],
            "reliability": "unknown",
        })
    return sources


def base_pack(
    agent_id: str,
    checkpoints: dict[str, dict[str, Any]],
    year: int,
    input_names: list[str],
    source_scope: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "agent_id": agent_id,
        "company_id": company_id_of(checkpoints),
        "report_year": year,
        "generated_at": now_iso(),
        "input_files": checkpoint_inputs(Path(checkpoints["_work_dir"]["path"]), input_names),
        "coverage": {
            "section_ids": AGENT_SECTION_IDS[agent_id],
            "time_periods": [str(year), str(year - 1), str(year - 2)],
            "source_scope": source_scope,
            "known_limits": [],
        },
        "key_findings": [],
        "evidence_facts": [],
        "calculations": [],
        "risk_chains": [],
        "tracking_signals": [],
        "external_sources": [],
        "missing_inputs": [],
        "review_required": False,
        "prohibited_content_hits": [],
    }


def build_evidence_curator(checkpoints: dict[str, dict[str, Any]], year: int) -> dict[str, Any]:
    snapshot = checkpoints["metric_snapshot.json"]
    inventory = checkpoints["wiki_inventory.json"]
    pack = base_pack(
        "evidence_curator",
        checkpoints,
        year,
        ["preflight.json", "wiki_inventory.json", "metric_snapshot.json", "evidence_package.json"],
        ["annual_report", "metrics", "evidence_index", "wiki_inventory"],
    )
    file_count = inventory.get("file_count")
    missing_required = inventory.get("missing_required_files") if isinstance(inventory.get("missing_required_files"), list) else []
    pack["key_findings"].append(key_finding(
        SECTION_IDS,
        f"本次报告已形成本地证据盘点，wiki_inventory file_count={file_count}，核心 metric_snapshot 与 evidence_package 可作为章节底稿来源。",
        0.86,
        "wiki_inventory.json",
        "用于保证 14 章都能回到本地证据或明确缺口。",
    ))
    pack["evidence_facts"] = metric_facts(snapshot, CORE_METRIC_KEYS[:6], year)
    if missing_required:
        pack["missing_inputs"].append(missing_input(
            "wiki_required_files",
            "wiki_inventory 标记部分必需文件缺失。",
            "相关章节必须保留证据缺口和审阅标记。",
            SECTION_IDS,
            "补齐缺失文件或在报告中降级表达。",
        ))
    for item in snapshot.get("missing_core_metrics", []) or []:
        pack["missing_inputs"].append(missing_input(
            str(item),
            "metric_snapshot.missing_core_metrics",
            "核心财务指标缺失会降低对应章节结论置信度。",
            ["executive_summary", "key_changes", "data_quality_traceability"],
        ))
    pack["risk_chains"].append(risk_chain(["证据缺口", "章节结论降级", "进入人工复核队列"], ["data_quality_traceability"], "medium"))
    pack["tracking_signals"].append(tracking_signal("证据包、引用页码和 metric_snapshot 一致性", ["data_quality_traceability"], "confirm", "pipeline validation"))
    pack["review_required"] = bool(pack["missing_inputs"])
    return pack


def build_financial_modeler(checkpoints: dict[str, dict[str, Any]], year: int) -> dict[str, Any]:
    snapshot = checkpoints["metric_snapshot.json"]
    outline = checkpoints["analysis_outline.json"]
    market = checkpoints["market_snapshot.json"]
    qualitative = checkpoints["qualitative_snapshot.json"]
    pack = base_pack(
        "financial_modeler",
        checkpoints,
        year,
        ["metric_snapshot.json", "evidence_package.json", "analysis_outline.json", "market_snapshot.json"],
        ["metrics", "derived_ratios", "valuation_anchors"],
    )
    core_judgment = str(outline.get("core_judgment") or "").strip()
    core_contradiction = str(outline.get("core_contradiction") or "").strip()
    if core_judgment:
        pack["key_findings"].append(key_finding(["executive_summary"], core_judgment, 0.82, "analysis_outline.json"))
    if core_contradiction:
        pack["key_findings"].append(key_finding(["executive_summary", "risk_chain_scenario"], core_contradiction, 0.8, "analysis_outline.json"))
    pack["key_findings"].extend(financial_diagnostic_findings(snapshot, outline, year))
    pack["key_findings"].extend(technology_rd_findings(snapshot, qualitative, year))
    pack["evidence_facts"] = metric_facts(snapshot, CORE_METRIC_KEYS, year)
    derived = outline.get("calculated_derived_metrics")
    if isinstance(derived, dict):
        for name, value in derived.items():
            pack["calculations"].append(calculation(str(name), "pipeline derived metric", {"source": "metric_snapshot.json"}, value, "analysis_outline.json"))
    pack["calculations"].extend(financial_model_calculations(snapshot, outline, year))
    pack["calculations"].extend(technology_rd_calculations(snapshot, year))
    revenue_yoy = yoy_value(snapshot, "operating_revenue", year)
    ocf_yoy = yoy_value(snapshot, "operating_cash_flow_net", year)
    free_cash_flow = metric_value(snapshot, "free_cash_flow", year)
    parent_profit = metric_value(snapshot, "parent_net_profit", year)
    deducted_profit = metric_value(snapshot, "deducted_parent_net_profit", year)
    if isinstance(revenue_yoy, (int, float)) and isinstance(ocf_yoy, (int, float)) and ocf_yoy < revenue_yoy:
        pack["risk_chains"].append(risk_chain(
            [
                f"营业收入同比 {fmt_number(revenue_yoy, '%')}",
                f"经营现金流同比 {fmt_number(ocf_yoy, '%')}",
                "收入增长未完全转化为经营现金流，需复核回款、库存、应收和供应链付款节奏",
            ],
            ["cash_flow_quality", "operating_quality", "risk_chain_scenario"],
            "high" if ocf_yoy < 0 else "medium",
        ))
    if isinstance(free_cash_flow, (int, float)) and free_cash_flow < 0:
        pack["risk_chains"].append(risk_chain(
            [
                f"自由现金流为 {fmt_number(free_cash_flow, '亿元')}",
                "资本开支或长期投入消耗经营现金流",
                "后续需验证融资、债务滚续和转型投入弹性",
            ],
            ["cash_flow_quality", "debt_liquidity", "risk_chain_scenario"],
            "high",
        ))
    if isinstance(parent_profit, (int, float)) and isinstance(deducted_profit, (int, float)):
        gap = parent_profit - deducted_profit
        if abs(gap) > max(abs(parent_profit) * 0.1, 1):
            pack["risk_chains"].append(risk_chain(
                [
                    f"归母与扣非差额 {fmt_number(gap, '亿元')}",
                    "非经常性因素可能放大或掩盖主营盈利变化",
                    "需跟踪扣非利润和经营现金流是否同向改善",
                ],
                ["profitability_and_cost", "risk_chain_scenario", "tracking_checklist"],
                "medium",
            ))
    for flag in outline.get("red_flags", []) or []:
        pack["risk_chains"].append(risk_chain([str(flag), "影响盈利质量、现金流或资产负债表安全边际"], ["risk_chain_scenario"], "high"))
    pack["risk_chains"].extend(technology_rd_risk_chains(snapshot, qualitative, year))
    for key, label, section_ids in [
        ("gross_margin", "毛利率", ["profitability_and_cost", "tracking_checklist"]),
        ("deducted_parent_net_profit", "扣非归母净利润", ["profitability_and_cost", "tracking_checklist"]),
        ("operating_cash_flow_net", "经营现金流净额", ["cash_flow_quality", "tracking_checklist"]),
        ("inventory", "存货", ["asset_quality_working_capital", "tracking_checklist"]),
        ("accounts_receivable", "应收账款", ["asset_quality_working_capital", "tracking_checklist"]),
    ]:
        value = metric_value(snapshot, key, year)
        if value is not None:
            pack["tracking_signals"].append(tracking_signal(
                f"{label}后续是否延续改善或恶化，当前值为 {fmt_number(value, metric_entry(snapshot, key).get('unit'))}",
                section_ids,
                "confirm",
                "metric_snapshot.json",
            ))
    pack["tracking_signals"].extend(technology_rd_tracking_signals(snapshot, qualitative, year))
    for text in (outline.get("improvement_items", []) or [])[:4]:
        pack["tracking_signals"].append(tracking_signal(str(text), ["tracking_checklist"], "improve", "analysis_outline.json"))
    for text in (outline.get("falsifying_evidence", []) or [])[:4]:
        pack["tracking_signals"].append(tracking_signal(str(text), ["tracking_checklist", "risk_chain_scenario"], "falsify", "analysis_outline.json"))
    if market and not market.get("strict_ok"):
        pack["missing_inputs"].append(missing_input(
            "market_snapshot",
            "本地股价、市值或估值快照不足。",
            "估值与预期差章节只能使用年报锚和条件判断，不能给确定性估值结论。",
            ["valuation_expectation_gap"],
            "补齐交易行情或估值分位快照。",
        ))
    pack["review_required"] = bool(pack["missing_inputs"])
    return pack


def build_business_strategy_researcher(checkpoints: dict[str, dict[str, Any]], year: int) -> dict[str, Any]:
    qualitative = checkpoints["qualitative_snapshot.json"]
    snapshot = checkpoints["metric_snapshot.json"]
    pack = base_pack(
        "business_strategy_researcher",
        checkpoints,
        year,
        ["preflight.json", "wiki_inventory.json", "qualitative_snapshot.json", "metric_snapshot.json"],
        ["semantic", "annual_report_business_profile", "strategy_claims", "strategy_financial_validation"],
    )
    items = qualitative_items_filtered(qualitative, ["strategy", "product_brand", "operation_driver", "rd_technology"], 12)
    for item in items[:6]:
        pack["key_findings"].append(strategy_finding_from_item(item, snapshot, year))
        pack["key_findings"].append(strategy_bridge_finding(item))
    pack["evidence_facts"] = [qualitative_fact(item, AGENT_SECTION_IDS["business_strategy_researcher"]) for item in items[:10]]
    pack["evidence_facts"].extend(metric_facts(snapshot, TECH_R_AND_D_METRIC_KEYS, year))
    pack["calculations"].extend(strategy_execution_calculations(snapshot, year))
    for item in qualitative_items_filtered(qualitative, ["external_risk"], 4):
        text = strategy_evidence_fact_text(item)
        theme, variables, signals = strategy_theme(text)
        pack["risk_chains"].append(risk_chain(
            [
                text,
                f"{theme}通过 {'、'.join(variables[:4])} 传导到报表",
                "若财务变量未验证，相关战略/政策判断必须降级",
            ],
            ["strategy_policy_external_risk", "risk_chain_scenario"],
            "medium",
        ))
        for signal in signals[:1]:
            pack["tracking_signals"].append(tracking_signal(signal, ["tracking_checklist"], "falsify", "metric_snapshot.json"))
    for item in items[:5]:
        theme, variables, _signals = strategy_theme(str(item.get("text") or ""))
        pack["tracking_signals"].append(tracking_signal(
            f"{theme}证据是否被{'、'.join(variables[:3])}验证",
            ["tracking_checklist"],
            "confirm",
            "qualitative_snapshot.json",
        ))
        pack["tracking_signals"].extend(strategy_tracking_signals(item))
    pack["risk_chains"].extend(technology_rd_risk_chains(snapshot, qualitative, year))
    pack["tracking_signals"].extend(technology_rd_tracking_signals(snapshot, qualitative, year))
    pack["missing_inputs"].extend(strategy_missing_inputs(qualitative))
    if not qualitative.get("strict_ok", True):
        pack["missing_inputs"].append(missing_input(
            "qualitative_snapshot_strict_ok",
            "定性语义证据未达到严格通过状态。",
            "业务战略章节需要保留人工复核或外部补证。",
            ["strategy_policy_external_risk", "tracking_checklist"],
        ))
    pack["review_required"] = bool(pack["missing_inputs"])
    return pack


def build_industry_peer_researcher(checkpoints: dict[str, dict[str, Any]], year: int) -> dict[str, Any]:
    peer_metrics = checkpoints["peer_metrics.json"]
    industry_research = checkpoints["industry_research.json"]
    pack = base_pack(
        "industry_peer_researcher",
        checkpoints,
        year,
        ["peer_metrics.json", "industry_research.json", "market_snapshot.json", "qualitative_snapshot.json"],
        ["peer_metrics", "external_search", "industry_trends"],
    )
    for text in peer_metrics.get("interpretation", []) or []:
        pack["key_findings"].append(key_finding(["industry_competition"], str(text), 0.76, "peer_metrics.json"))
    for text in industry_research.get("interpretation", []) or []:
        pack["key_findings"].append(key_finding(["industry_competition", "strategy_policy_external_risk"], str(text), 0.64, "industry_research.json"))
    peer_aggregates = peer_metrics.get("aggregates")
    if not peer_aggregates:
        peer_aggregates = {
            "status": "missing",
            "reason": "peer_metrics.aggregates unavailable",
            "fallback": "industry peer section must downgrade to directional discussion",
        }
    pack["calculations"].append(calculation(
        "peer_metrics_aggregates",
        "peer_metrics_builder.py aggregate output",
        {"selection_method": peer_metrics.get("selection_method"), "peer_count": peer_metrics.get("peer_count")},
        peer_aggregates,
        "peer_metrics.json",
    ))
    if isinstance(peer_aggregates, dict):
        for metric_key, aggregate in peer_aggregates.items():
            if not isinstance(aggregate, dict):
                continue
            finding = peer_percentile_finding(metric_key, aggregate)
            if finding:
                pack["key_findings"].append(finding)
        strengths, weaknesses = peer_strength_weaknesses(peer_aggregates)
        if strengths:
            pack["key_findings"].append(key_finding(
                ["industry_competition", "valuation_expectation_gap"],
                "同业相对优势：" + "；".join(strengths) + "。优势项只能作为预期差线索，仍需结合盈利质量和现金流复核。",
                0.72,
                "peer_metrics.json",
                "peer_strengths",
            ))
        if weaknesses:
            pack["key_findings"].append(key_finding(
                ["industry_competition", "risk_chain_scenario"],
                "同业相对短板：" + "；".join(weaknesses) + "，需要解释是否由产品结构、价格竞争、转型投入或营运资本占用造成。",
                0.72,
                "peer_metrics.json",
                "peer_weaknesses",
            ))
        pack["risk_chains"].extend(peer_metric_risk_chains(peer_aggregates))
        pack["tracking_signals"].extend(peer_tracking_signals(peer_aggregates))
    pack["external_sources"] = external_sources(industry_research)
    for warning in peer_metrics.get("peer_selection_warnings", peer_metrics.get("warnings", [])) or []:
        pack["missing_inputs"].append(missing_input(
            "peer_selection",
            str(warning),
            "同业比较样本需降级表达，避免把宽口径样本当作严格同业。",
            ["industry_competition"],
            "补齐同行业公司或重新运行 peer_metrics_builder.py。",
        ))
    if not peer_metrics.get("strict_ok", False):
        pack["missing_inputs"].append(missing_input(
            "peer_metrics_strict_ok",
            "同业样本未达到严格通过状态。",
            "行业竞争位置只能方向性表达。",
            ["industry_competition"],
        ))
    if not industry_research.get("strict_ok", False):
        pack["missing_inputs"].append(missing_input(
            "industry_peer_external_sources",
            "外部行业检索未达到严格通过状态。",
            "行业趋势和政策变量必须标注待核验。",
            ["industry_competition", "strategy_policy_external_risk"],
            "优先补 Hermes Tavily/EXA 的权威行业来源。",
        ))
    for text in (industry_research.get("warnings", []) or [])[:4]:
        pack["risk_chains"].append(risk_chain([str(text), "行业结论置信度下降"], ["industry_competition"], "medium"))
    pack["tracking_signals"].append(tracking_signal("同业样本行业路径、peer_count、strict_ok", ["industry_competition"], "confirm", "peer_metrics.json"))
    pack["review_required"] = bool(pack["missing_inputs"])
    return pack


def build_governance_risk_researcher(checkpoints: dict[str, dict[str, Any]], year: int) -> dict[str, Any]:
    qualitative = checkpoints["qualitative_snapshot.json"]
    outline = checkpoints["analysis_outline.json"]
    pack = base_pack(
        "governance_risk_researcher",
        checkpoints,
        year,
        ["qualitative_snapshot.json", "analysis_outline.json", "evidence_package.json", "preflight.json"],
        ["semantic_governance", "risk_flags", "traceability"],
    )
    governance_items = qualitative_items(qualitative, ["governance", "external_risk"], 10)
    for item in governance_items[:6]:
        pack["key_findings"].append(key_finding(
            AGENT_SECTION_IDS["governance_risk_researcher"],
            str(item.get("text") or "").strip(),
            0.7,
            "qualitative_snapshot.json",
            str(item.get("kind") or ""),
        ))
    pack["evidence_facts"] = [qualitative_fact(item, AGENT_SECTION_IDS["governance_risk_researcher"]) for item in governance_items[:8]]
    for flag in outline.get("red_flags", []) or []:
        pack["risk_chains"].append(risk_chain([str(flag), "触发财务安全边际或治理复核要求"], ["risk_chain_scenario"], "high"))
    for item in governance_items[:4]:
        pack["tracking_signals"].append(tracking_signal(str(item.get("text") or "")[:180], ["tracking_checklist"], "confirm", "qualitative_snapshot.json"))
    if not governance_items:
        pack["missing_inputs"].append(missing_input(
            "governance_semantic_evidence",
            "qualitative_snapshot 未提供治理或外部风险证据。",
            "治理合规章节需人工补证或保留空白降级。",
            ["governance_compliance_shareholders"],
        ))
    pack["review_required"] = bool(pack["missing_inputs"])
    return pack


BUILDERS = {
    "evidence_curator": build_evidence_curator,
    "financial_modeler": build_financial_modeler,
    "business_strategy_researcher": build_business_strategy_researcher,
    "industry_peer_researcher": build_industry_peer_researcher,
    "governance_risk_researcher": build_governance_risk_researcher,
}


def build_manifest(work_dir: Path, output_dir: Path, packs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_by": "generate_research_packs.py",
        "generated_at": now_iso(),
        "work_dir": str(work_dir),
        "research_packs_dir": str(output_dir),
        "implementation_stage": "deterministic_checkpoint_scaffold",
        "agent_ids": [pack["agent_id"] for pack in packs],
        "pack_files": {
            pack["agent_id"]: str(output_dir / f"{pack['agent_id']}.json")
            for pack in packs
        },
        "review_required_agent_ids": [pack["agent_id"] for pack in packs if pack.get("review_required")],
        "missing_input_count": sum(len(pack.get("missing_inputs", [])) for pack in packs),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate siq_analysis research_packs from report checkpoints.")
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--year", type=int, default=2025)
    parser.add_argument("--output-dir", type=Path, help="Default: <work-dir>/research_packs")
    parser.add_argument("--write-manifest", type=Path, help="Default: <work-dir>/research_pack_manifest.json")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    work_dir = args.work_dir
    output_dir = args.output_dir or work_dir / "research_packs"
    manifest_path = args.write_manifest or work_dir / "research_pack_manifest.json"
    checkpoints = read_checkpoints(work_dir)
    checkpoints["_work_dir"] = {"path": str(work_dir)}

    packs: list[dict[str, Any]] = []
    failures: list[str] = []
    for agent_id in RESEARCH_AGENT_IDS:
        try:
            pack = BUILDERS[agent_id](checkpoints, args.year)
        except Exception as exc:  # pragma: no cover - defensive operational guard
            failures.append(f"{agent_id}:build_failed:{exc}")
            continue
        output_path = output_dir / f"{agent_id}.json"
        dump_json(output_path, pack)
        packs.append(pack)

    manifest = build_manifest(work_dir, output_dir, packs)
    dump_json(manifest_path, manifest)
    result = {
        "ok": not failures and len(packs) == len(RESEARCH_AGENT_IDS),
        "stage": "completed" if not failures else "build_failed",
        "work_dir": str(work_dir),
        "research_packs_dir": str(output_dir),
        "manifest": str(manifest_path),
        "pack_count": len(packs),
        "failures": failures,
        "manifest_summary": manifest,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
