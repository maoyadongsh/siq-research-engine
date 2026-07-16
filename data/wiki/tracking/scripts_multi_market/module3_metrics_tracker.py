#!/usr/bin/env python3
"""
模块3: 指标追踪器
追踪季度财报指标变化，计算环比/同比/偏离度，输出指标追踪面板。
"""

import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_SCRIPT_PATH = Path(__file__).resolve()
_PROJECT_ROOT = _SCRIPT_PATH.parents[4]
DEFAULT_WIKI_BASE = str(Path(
    os.environ.get("SIQ_WIKI_ROOT")
    or os.environ.get("WIKI_ROOT")
    or _SCRIPT_PATH.parents[2]
).expanduser().resolve())
WIKISET_DIR = Path(
    os.environ.get("SIQ_WIKISET_ROOT")
    or os.environ.get("WIKISET_ROOT")
    or _PROJECT_ROOT / "scripts" / "wiki" / "wikiset"
).expanduser().resolve()
if str(WIKISET_DIR) not in sys.path:
    sys.path.insert(0, str(WIKISET_DIR))

from company_identity import company_dir_path
from local_citations import format_ref_summary, resolve_key_metric_refs


# 核心财务指标定义
CORE_METRICS = {
    "revenue": {"name": "营业收入", "unit": "", "direction": "up"},
    "operating_revenue": {"name": "营业收入", "unit": "元", "direction": "up", "display_unit": "亿元"},
    "net_income": {"name": "净利润", "unit": "", "direction": "up"},
    "net_income_attributable_to_parent": {"name": "归母净利润", "unit": "", "direction": "up"},
    "net_profit": {"name": "净利润", "unit": "元", "direction": "up", "display_unit": "亿元"},
    "parent_net_profit": {"name": "归母净利润", "unit": "元", "direction": "up", "display_unit": "亿元"},
    "gross_profit_margin": {"name": "毛利率", "unit": "%", "direction": "up"},
    "net_profit_margin": {"name": "净利率", "unit": "%", "direction": "up"},
    "roe": {"name": "净资产收益率ROE", "unit": "%", "direction": "up"},
    "roa": {"name": "总资产收益率ROA", "unit": "%", "direction": "up"},
    "debt_ratio": {"name": "资产负债率", "unit": "%", "direction": "down"},
    "current_ratio": {"name": "流动比率", "unit": "倍", "direction": "up"},
    "quick_ratio": {"name": "速动比率", "unit": "倍", "direction": "up"},
    "inventory_turnover": {"name": "存货周转率", "unit": "次", "direction": "up"},
    "receivable_turnover": {"name": "应收账款周转率", "unit": "次", "direction": "up"},
    "cash_flow_operating": {"name": "经营活动现金流", "unit": "元", "direction": "up", "display_unit": "亿元"},
    "operating_cash_flow_net": {"name": "经营活动现金流", "unit": "元", "direction": "up", "display_unit": "亿元"},
    "operating_cash_flow": {"name": "经营活动现金流", "unit": "", "direction": "up"},
    "total_assets": {"name": "总资产", "unit": "", "direction": "up"},
    "total_liabilities": {"name": "总负债", "unit": "", "direction": "down"},
    "cash_and_cash_equivalents": {"name": "现金及现金等价物", "unit": "", "direction": "up"},
    "eps": {"name": "每股收益EPS", "unit": "元", "direction": "up"},
    "basic_eps": {"name": "每股收益EPS", "unit": "元", "direction": "up"},
}

AMOUNT_METRICS = {
    "revenue",
    "operating_revenue",
    "net_profit",
    "parent_net_profit",
    "cash_flow_operating",
    "operating_cash_flow_net",
    "net_income",
    "net_income_attributable_to_parent",
    "operating_cash_flow",
    "total_assets",
    "total_liabilities",
    "cash_and_cash_equivalents",
}


def _to_number(value):
    """尽量把财务 JSON 中的值转换为数字，失败则保留原值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def format_metric_value(
    value,
    canonical: str,
    source_unit: str,
    config: Dict,
    *,
    currency: str = "",
    value_basis: str = "",
) -> str:
    """按来源单位生成可读展示，避免把元误写成百万元。"""
    if value in (None, ""):
        return "N/A"
    number = _to_number(value)
    if not isinstance(number, float):
        return f"{value} {source_unit or config.get('unit', '')}".strip()

    unit = source_unit or config.get("unit", "")
    if canonical in AMOUNT_METRICS:
        if unit in {"元", "人民币元", "CNY", "RMB"}:
            return f"{number / 100_000_000:,.2f} 亿元"
        if unit == "万元":
            return f"{number / 10_000:,.2f} 亿元"
        if unit == "百万元":
            return f"{number / 100:,.2f} 亿元"
        if unit == "人民币百万元":
            divisor = 100_000_000 if value_basis == "normalized_base" else 100
            return f"{number / divisor:,.2f} 亿元"
        if unit == "亿元":
            return f"{number:,.2f} 亿元"
        upper_unit = unit.upper()
        if re.fullmatch(r"[A-Z]{3}", upper_unit):
            magnitude = abs(number)
            if magnitude >= 1_000_000_000:
                return f"{number / 1_000_000_000:,.2f} {upper_unit} billion"
            if magnitude >= 1_000_000:
                return f"{number / 1_000_000:,.2f} {upper_unit} million"
            return f"{number:,.2f} {upper_unit}"
        if currency and unit.lower() in {"million", "billion", "thousand"}:
            return f"{number:,.2f} {currency.upper()} {unit.lower()}"

    if unit == "%":
        return f"{number:,.2f}%"
    if unit:
        return f"{number:,.2f} {unit}"
    return f"{number:,.2f}"


def resolve_display_unit(canonical: str, source_unit: str, currency: str, config: Dict) -> str:
    """Return a market-aware display unit for structured/raw output."""
    unit = str(source_unit or "").strip()
    currency_code = str(currency or "").strip().upper()
    if canonical in AMOUNT_METRICS:
        if currency_code in {"CNY", "RMB"} or unit in {
            "元",
            "人民币元",
            "万元",
            "百万元",
            "人民币百万元",
            "亿元",
        }:
            return "亿元"
        if currency_code and unit.lower() in {"million", "billion", "thousand"}:
            return f"{currency_code} {unit.lower()}"
        return unit or currency_code
    return unit or str(config.get("unit") or "")


def _normalized_source_ref(metric: Dict) -> Dict:
    source = metric.get("source") if isinstance(metric.get("source"), dict) else {}
    raw = metric.get("raw") if isinstance(metric.get("raw"), dict) else {}
    raw_payload = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
    ref = {
        "source_type": source.get("source_type") or ("xbrl_fact" if metric.get("raw_fact_id") else "normalized_metric"),
        "report_id": source.get("report_id") or os.environ.get("SIQ_TRACKING_REPORT_ID"),
        "task_id": source.get("task_id"),
        "pdf_page": source.get("pdf_page") or source.get("pdf_page_number"),
        "table_index": source.get("table_index"),
        "md_line": source.get("md_line"),
        "source_url": source.get("source_url") or raw.get("source_url"),
        "html_anchor": source.get("html_anchor") or raw_payload.get("anchor"),
        "xbrl_fact_id": metric.get("raw_fact_id") or raw_payload.get("fact_id") or raw_payload.get("id"),
        "xbrl_concept": metric.get("concept") or raw.get("concept"),
        "xbrl_context": raw.get("context_id") or raw_payload.get("context_ref") or raw_payload.get("contextRef"),
        "xbrl_unit": metric.get("currency") or metric.get("unit"),
        "quote": source.get("quote_text") or source.get("quote"),
    }
    identity_raw = os.environ.get("SIQ_TRACKING_RESEARCH_IDENTITY", "")
    try:
        identity = json.loads(identity_raw) if identity_raw else None
    except json.JSONDecodeError:
        identity = None
    if isinstance(identity, dict):
        ref["research_identity"] = identity
    return {key: value for key, value in ref.items() if value not in (None, "")}


def _normalized_unit(metric: Dict) -> str:
    unit = str(metric.get("unit") or "").strip()
    currency = str(metric.get("currency") or "").strip().upper()
    scale = str(metric.get("scale") or "").strip()
    if unit and unit.lower() != "reported":
        return unit
    if currency and scale in {"1000000000", "1e9"}:
        return f"{currency} billion"
    if currency and scale in {"1000000", "1e6"}:
        return f"{currency} million"
    return currency or unit


def _duration_bucket(value) -> str:
    if value in (None, "", 0, "0"):
        return "instant"
    try:
        days = int(value)
    except (TypeError, ValueError):
        return "unknown"
    if 330 <= days <= 400:
        return "annual"
    if 240 <= days <= 300:
        return "nine_month"
    if 150 <= days <= 210:
        return "half_year"
    if 70 <= days <= 110:
        return "quarter"
    return f"days_{days}"


def normalize_metrics_payload(payload: Dict) -> Dict:
    """Project normalized market metrics into the legacy tracker shape in memory."""

    if isinstance(payload.get("data"), list):
        return {
            **payload,
            "data": [
                {**item, "value_basis": item.get("value_basis") or "normalized_base"}
                if isinstance(item, dict)
                else item
                for item in payload["data"]
            ],
        }
    rows = payload.get("metrics")
    if not isinstance(rows, list):
        return {"data": []}
    grouped: Dict[Tuple[str, str, str, str], Dict] = {}
    for metric in rows:
        if not isinstance(metric, dict):
            continue
        if str(metric.get("segment_key") or metric.get("scope") or "consolidated") not in {"", "consolidated"}:
            continue
        canonical = str(metric.get("canonical_name") or metric.get("metric_key") or "").strip()
        period = str(metric.get("period_key") or metric.get("period") or metric.get("period_end") or "").strip()
        value = metric.get("value", metric.get("normalized_value", metric.get("raw_value")))
        unit = _normalized_unit(metric)
        currency = str(metric.get("currency") or "").strip().upper()
        accounting_standard = str(
            metric.get("accounting_standard")
            or os.environ.get("SIQ_TRACKING_ACCOUNTING_STANDARD")
            or ""
        ).strip()
        period_basis = ":".join(
            str(value or "")
            for value in (
                metric.get("qtd_ytd_type") or "unknown",
                metric.get("fiscal_period") or "unknown",
                _duration_bucket(metric.get("duration_days")),
            )
        )
        if not canonical or not period or value in (None, ""):
            continue
        key = (canonical, unit, accounting_standard, period_basis)
        item = grouped.setdefault(
            key,
            {
                "canonical_name": canonical,
                "name": metric.get("metric_name") or metric.get("label") or metric.get("local_name") or canonical,
                "unit": unit,
                "currency": currency,
                "accounting_standard": accounting_standard,
                "period_basis": period_basis,
                "scale": metric.get("scale", 1),
                "value_basis": "reported_unit",
                "values": {},
                "evidence_refs_by_period": {},
            },
        )
        item["values"][period] = _to_number(value)
        ref = _normalized_source_ref(metric)
        if ref:
            item["evidence_refs_by_period"].setdefault(period, []).append(ref)
    return {"data": list(grouped.values()), "source_schema_version": payload.get("schema_version")}


def load_metrics(metrics_path: str) -> Dict:
    """加载并规范化指标数据，不回写源文件。"""
    with open(metrics_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    return normalize_metrics_payload(payload if isinstance(payload, dict) else {})


def calculate_changes(values: Dict[str, float]) -> Dict:
    """
    计算指标变化率

    输入: {year: value, ...}
    输出: {
        "yoy": {year: change_pct, ...},  # 同比
        "qoq": {year: change_pct, ...},  # 环比 (如有多季度数据)
        "cagr": float,  # 复合年增长率
        "latest_yoy": float,  # 最新同比
        "latest_value": float,  # 最新值
        "previous_value": float,  # 上期值
    }
    """
    years = sorted(values.keys())
    if len(years) < 2:
        return {}
    if any(not isinstance(values[key], (int, float)) or isinstance(values[key], bool) for key in years):
        return {}

    result = {
        "yoy": {},
        "latest_yoy": None,
        "latest_value": values[years[-1]],
        "previous_value": values[years[-2]],
    }

    # 计算同比
    for i in range(1, len(years)):
        year = years[i]
        prev_year = years[i-1]
        curr = values[year]
        prev = values[prev_year]

        if prev and prev != 0:
            yoy = (curr - prev) / abs(prev) * 100
            result["yoy"][year] = round(yoy, 2)
        else:
            result["yoy"][year] = None

    result["latest_yoy"] = result["yoy"].get(years[-1])

    # 计算 CAGR (复合年增长率)
    if len(years) >= 2:
        first_match = re.search(r"(?:^|\D)(20\d{2})(?:\D|$)", str(years[0]))
        last_match = re.search(r"(?:^|\D)(20\d{2})(?:\D|$)", str(years[-1]))
        first_year = int(first_match.group(1)) if first_match else None
        last_year = int(last_match.group(1)) if last_match else None
        first_val = values[years[0]]
        last_val = values[years[-1]]

        if (
            first_val
            and first_val > 0
            and last_val
            and last_val > 0
            and first_year is not None
            and last_year is not None
            and last_year > first_year
        ):
            n = last_year - first_year
            cagr = ((last_val / first_val) ** (1/n) - 1) * 100
            result["cagr"] = round(cagr, 2)
        else:
            # 指标跨正负号或包含零值时 CAGR 没有稳定经济含义，避免生成复数或误导结论。
            result["cagr"] = None

    return result


def calculate_deviation(current: float, benchmark: float) -> float:
    """计算偏离度 (与行业均值或历史均值比较)"""
    if benchmark and benchmark != 0:
        return round((current - benchmark) / abs(benchmark) * 100, 2)
    return None


def assess_trend(changes: Dict, metric_config: Dict) -> str:
    """评估指标趋势"""
    latest_yoy = changes.get("latest_yoy")
    if latest_yoy is None:
        return "unknown"

    direction = metric_config.get("direction", "up")

    # 根据方向判断好坏
    if direction == "up":
        if latest_yoy > 10:
            return "strong_positive"
        elif latest_yoy > 0:
            return "positive"
        elif latest_yoy > -10:
            return "negative"
        else:
            return "strong_negative"
    else:  # direction == "down"
        if latest_yoy < -10:
            return "strong_positive"
        elif latest_yoy < 0:
            return "positive"
        elif latest_yoy < 10:
            return "negative"
        else:
            return "strong_negative"


ASSESSMENT_BY_TREND = {
    "strong_positive": "strong_favorable",
    "positive": "favorable",
    "negative": "unfavorable",
    "strong_negative": "strong_unfavorable",
    "unknown": "unknown",
}


def classify_movement(latest_yoy) -> str:
    """Classify the observed movement independently from whether it is favorable."""
    try:
        value = float(latest_yoy)
    except (TypeError, ValueError):
        return "unknown"
    if not math.isfinite(value):
        return "unknown"
    if round(value, 2) == 0:
        return "flat"
    return "increase" if value > 0 else "decrease"


def render_trend_interpretation(metric: Dict) -> str:
    """Render factual movement first, then the separate directional assessment."""
    latest_yoy = metric.get("latest_yoy")
    movement = metric.get("movement") or classify_movement(latest_yoy)
    assessment = metric.get("assessment") or ASSESSMENT_BY_TREND.get(
        metric.get("trend", "unknown"),
        "unknown",
    )
    if movement == "flat":
        assessment = "neutral"
    name = metric.get("name") or metric.get("canonical_name") or "指标"
    if movement == "unknown":
        return f"⚪ **趋势解读**: {name}缺少可比期间，无法判断同比方向或变化影响。"

    value = float(latest_yoy)
    magnitude = abs(round(value, 2))
    if movement == "flat":
        movement_copy = f"同比持平（{magnitude:.2f}%）"
    else:
        direction_copy = "上升" if movement == "increase" else "下降"
        modifier = "大幅" if magnitude > 10 else ""
        movement_copy = f"同比{modifier}{direction_copy} {magnitude:.2f}%"

    assessment_copy = {
        "strong_favorable": "从该指标的期望方向看，变化显著有利。",
        "favorable": "从该指标的期望方向看，变化有利。",
        "unfavorable": "从该指标的期望方向看，变化不利，需关注。",
        "strong_unfavorable": "从该指标的期望方向看，变化显著不利，存在风险。",
        "neutral": "按展示精度看没有变化，影响保持中性。",
        "unknown": "当前无法评价该变化的影响。",
    }[assessment]
    emoji = {
        "strong_favorable": "✅",
        "favorable": "✅",
        "unfavorable": "⚠️",
        "strong_unfavorable": "🔴",
        "neutral": "⚪",
        "unknown": "⚪",
    }[assessment]
    return f"{emoji} **趋势解读**: {name}{movement_copy}；{assessment_copy}"


def validate_metrics_panel_semantics(content: str, tracked_metrics: List[Dict]) -> List[str]:
    """Fail closed when rendered prose no longer matches structured movement facts."""
    issues = []
    for metric in tracked_metrics:
        expected = render_trend_interpretation(metric)
        if expected not in content:
            issues.append(
                f"trend_interpretation_mismatch:{metric.get('canonical_name') or metric.get('name')}"
            )
    return issues


def generate_metrics_panel(
    stock_code: str,
    company_name: str,
    metrics_path: str,
    output_dir: str,
    company_dir: str = None,
    period: str = None,
) -> str:
    """
    生成指标追踪面板

    输出：wiki/tracking/<stock_code>-<company>/metrics/<period>.md
    """
    if period is None:
        # 自动推断最新季度/年度
        now = datetime.now()
        quarter = (now.month - 1) // 3 + 1
        if quarter == 1:
            period = f"{now.year - 1}-Q4"
        else:
            period = f"{now.year}-Q{quarter - 1}"

    os.makedirs(output_dir, exist_ok=True)

    # 加载数据
    metrics_data = load_metrics(metrics_path)
    data_list = metrics_data.get("data", [])

    # 构建指标追踪结果
    tracked_metrics = []

    for metric in data_list:
        canonical = metric.get("canonical_name", "")
        if canonical not in CORE_METRICS:
            continue

        config = CORE_METRICS[canonical]
        values = metric.get("values", {})
        source_unit = metric.get("unit") or config.get("unit", "")

        if not values:
            continue

        # 计算变化
        changes = calculate_changes(values)
        if not changes:
            latest_year = sorted(values.keys(), reverse=True)[0]
            latest_value = values[latest_year]
            if not isinstance(latest_value, (int, float)) or isinstance(latest_value, bool):
                continue
            changes = {
                "yoy": {},
                "latest_yoy": None,
                "latest_value": latest_value,
                "previous_value": None,
                "cagr": None,
            }

        latest_year = sorted(values.keys(), reverse=True)[0]
        refs_by_period = metric.get("evidence_refs_by_period") if isinstance(metric.get("evidence_refs_by_period"), dict) else {}
        source_refs = list(refs_by_period.get(latest_year) or [])
        if company_dir and not source_refs:
            source_refs = resolve_key_metric_refs(Path(company_dir), metric.get("name") or canonical, latest_year)

        trend = assess_trend(changes, config)
        movement = classify_movement(changes.get("latest_yoy"))
        assessment = "neutral" if movement == "flat" else ASSESSMENT_BY_TREND[trend]

        tracked = {
            "canonical_name": canonical,
            "name": config["name"],
            "unit": source_unit,
            "currency": metric.get("currency"),
            "accounting_standard": metric.get("accounting_standard"),
            "period_basis": metric.get("period_basis"),
            "scale": metric.get("scale", 1),
            "value_basis": metric.get("value_basis") or "reported_unit",
            "display_unit": resolve_display_unit(
                canonical,
                source_unit,
                metric.get("currency"),
                config,
            ),
            "direction": config["direction"],
            "values": values,
            "changes": changes,
            "trend": trend,
            "movement": movement,
            "assessment": assessment,
            "comparison_status": "comparable" if changes.get("latest_yoy") is not None else "insufficient_periods",
            "latest_value": changes["latest_value"],
            "previous_value": changes["previous_value"],
            "latest_yoy": changes.get("latest_yoy"),
            "cagr": changes.get("cagr"),
            "source_refs": source_refs,
        }
        tracked_metrics.append(tracked)

    # 生成 Markdown
    output_path = os.path.join(output_dir, f"{period}.md")
    staged_path = output_path + ".tmp"

    with open(staged_path, 'w', encoding='utf-8') as f:
        f.write(f"# {company_name} ({stock_code}) 指标追踪面板\n\n")
        f.write(f"> 报告期: {period}\n")
        f.write(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

        # 摘要
        f.write("## 指标摘要\n\n")

        positive_count = sum(1 for m in tracked_metrics if m["trend"] in ["positive", "strong_positive"])
        negative_count = sum(1 for m in tracked_metrics if m["trend"] in ["negative", "strong_negative"])
        comparable_count = sum(1 for m in tracked_metrics if m["comparison_status"] == "comparable")
        single_period_count = len(tracked_metrics) - comparable_count
        located_metric_count = sum(1 for m in tracked_metrics if m.get("source_refs"))

        f.write(f"- **正向指标**: {positive_count} 项\n")
        f.write(f"- **负向指标**: {negative_count} 项\n")
        f.write(f"- **追踪指标数**: {len(tracked_metrics)} 项\n")
        f.write(f"- **可比较指标**: {comparable_count} 项\n")
        f.write(f"- **仅最新期指标**: {single_period_count} 项\n")
        f.write(f"- **具备来源定位**: {located_metric_count} 项\n\n")
        if not tracked_metrics:
            f.write("> **数据状态**: 未加载到可追踪的结构化指标，无法判定趋势或预警。\n\n")
        elif comparable_count == 0:
            f.write("> **数据状态**: 已加载最新期指标，但缺少可比期间，无法判定同比趋势或据此排除预警。\n\n")

        # 关键指标概览表
        f.write("## 关键指标概览\n\n")
        f.write("| 指标 | 最新值 | 上期值 | 同比变动 | CAGR | 趋势 |\n")
        f.write("|------|--------|--------|----------|------|------|\n")

        trend_emojis = {
            "strong_positive": "🟢🟢",
            "positive": "🟢",
            "negative": "🟡",
            "strong_negative": "🔴",
            "unknown": "⚪",
        }

        for m in tracked_metrics:
            latest = m["latest_value"]
            previous = m["previous_value"]
            yoy = m.get("latest_yoy")
            cagr = m.get("cagr")
            trend = m["trend"]

            latest_str = format_metric_value(
                latest,
                m["canonical_name"],
                m["unit"],
                CORE_METRICS[m["canonical_name"]],
                currency=m.get("currency") or "",
                value_basis=m.get("value_basis") or "",
            )
            prev_str = format_metric_value(
                previous,
                m["canonical_name"],
                m["unit"],
                CORE_METRICS[m["canonical_name"]],
                currency=m.get("currency") or "",
                value_basis=m.get("value_basis") or "",
            )
            yoy_str = f"{yoy:+.2f}%" if yoy is not None else "N/A"
            cagr_str = f"{cagr:+.2f}%" if cagr is not None else "N/A"
            emoji = trend_emojis.get(trend, "⚪")

            f.write(f"| {m['name']} | {latest_str} | {prev_str} | {yoy_str} | {cagr_str} | {emoji} |\n")

        f.write("\n")

        # 详细分析
        f.write("## 详细分析\n\n")

        for m in tracked_metrics:
            emoji = trend_emojis.get(m["trend"], "⚪")
            f.write(f"### {emoji} {m['name']}\n\n")

            # 历史数据表
            f.write("**历史数据**:\n\n")
            f.write("| 年度 | 数值 | 同比变动 |\n")
            f.write("|------|------|----------|\n")

            years = sorted(m["values"].keys(), reverse=True)
            for year in years:
                val = m["values"][year]
                yoy = m["changes"].get("yoy", {}).get(year)
                val_str = format_metric_value(
                    val,
                    m["canonical_name"],
                    m["unit"],
                    CORE_METRICS[m["canonical_name"]],
                    currency=m.get("currency") or "",
                    value_basis=m.get("value_basis") or "",
                )
                yoy_str = f"{yoy:+.2f}%" if yoy is not None else "N/A"
                f.write(f"| {year} | {val_str} | {yoy_str} |\n")

            f.write("\n")

            # 事实方向由同比符号决定；好坏评价单独使用指标期望方向。
            f.write(render_trend_interpretation(m) + "\n\n")

            source_refs = m.get("source_refs") or []
            if source_refs:
                f.write(f"**来源**: {format_ref_summary(source_refs[:3])}\n")
                for ref in source_refs[:3]:
                    links = []
                    if ref.get("open_pdf_page_url"):
                        links.append(f"[打开PDF页]({ref['open_pdf_page_url']})")
                    if ref.get("open_source_page_url"):
                        links.append(f"[查看页来源]({ref['open_source_page_url']})")
                    if ref.get("open_source_table_url"):
                        links.append(f"[查看表格]({ref['open_source_table_url']})")
                    if links:
                        f.write("- " + "，".join(links) + "\n")
                f.write("\n")

            f.write("---\n\n")

        # 原始数据附录
        f.write("## 原始数据\n\n")
        f.write("```json\n")
        f.write(json.dumps(tracked_metrics, ensure_ascii=False, indent=2))
        f.write("\n```\n")

    staged_content = Path(staged_path).read_text(encoding="utf-8")
    semantic_issues = validate_metrics_panel_semantics(staged_content, tracked_metrics)
    if semantic_issues:
        Path(staged_path).unlink(missing_ok=True)
        raise ValueError("tracking metric semantic validation failed: " + ", ".join(semantic_issues))
    os.replace(staged_path, output_path)

    print(f"✅ 指标追踪面板已生成: {output_path}")
    print(f"   共追踪 {len(tracked_metrics)} 项指标")
    return output_path


def run_metrics_tracker(
    stock_code: str,
    company_name: str,
    wiki_base: str = DEFAULT_WIKI_BASE,
    period: str = None,
    use_search: bool = False,
) -> str:
    """
    主入口：运行指标追踪

    输入：wiki/companies/<stock_code>-<company>/metrics/key_metrics.json
    输出：wiki/tracking/<stock_code>-<company>/metrics/<period>.md

    Args:
        use_search: 是否使用网络搜索补充行业对比数据
    """
    company_dir_path_value = company_dir_path(wiki_base, stock_code, company_name).resolve()
    company_dir = str(company_dir_path_value)
    report_dir_raw = str(os.environ.get("SIQ_TRACKING_REPORT_DIR") or "").strip()
    report_dir = Path(report_dir_raw).expanduser().resolve() if report_dir_raw else None
    explicit_metrics_raw = str(os.environ.get("SIQ_TRACKING_METRICS_PATH") or "").strip()
    explicit_metrics = Path(explicit_metrics_raw).expanduser().resolve() if explicit_metrics_raw else None
    candidates = []
    if explicit_metrics:
        candidates.append(explicit_metrics)
    if report_dir:
        candidates.extend(
            [
                report_dir / "metrics" / "normalized_metrics.json",
                report_dir / "metrics" / "key_metrics.json",
                report_dir / "metrics" / "financial_data.json",
            ]
        )
        report_id = report_dir.name
        candidates.extend(
            [
                company_dir_path_value / "metrics" / "reports" / report_id / "normalized_metrics.json",
                company_dir_path_value / "metrics" / "reports" / report_id / "key_metrics.json",
            ]
        )
    candidates.extend(
        [
            company_dir_path_value / "metrics" / "latest" / "normalized_metrics.json",
            company_dir_path_value / "metrics" / "latest" / "key_metrics.json",
            company_dir_path_value / "metrics" / "normalized_metrics.json",
            company_dir_path_value / "metrics" / "key_metrics.json",
        ]
    )
    metrics_file = None
    for candidate in candidates:
        try:
            candidate.relative_to(company_dir_path_value)
        except ValueError:
            continue
        if candidate.is_file():
            metrics_file = candidate
            break
    metrics_path = str(metrics_file) if metrics_file else ""

    tracking_dir = os.path.join(company_dir, "tracking")
    metrics_dir = os.path.join(tracking_dir, "metrics")

    if not metrics_path:
        print(f"❌ 指标数据不存在: {company_dir}")
        return None

    # 如果使用搜索，尝试获取行业对比数据
    if use_search:
        try:
            from search_tools import SearchTools
            search = SearchTools()
            availability = search.check_availability()

            if availability.get("any"):
                print(f"🔍 搜索行业对比数据...")
                # 这里可以添加行业对比数据的获取逻辑
                # 例如：搜索行业平均毛利率、ROE 等
                pass
        except ImportError:
            print("⚠️ search_tools 模块未找到，跳过行业数据搜索")

    return generate_metrics_panel(stock_code, company_name, metrics_path, metrics_dir, company_dir, period)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="指标追踪器")
    parser.add_argument("--stock", required=True, help="股票代码")
    parser.add_argument("--company", required=True, help="公司简称")
    parser.add_argument("--period", help="报告期 (如 2025-Q1)")
    parser.add_argument("--wiki-base", default=DEFAULT_WIKI_BASE, help="wiki 根目录")
    args = parser.parse_args()

    run_metrics_tracker(args.stock, args.company, args.wiki_base, args.period)
